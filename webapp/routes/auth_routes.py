# -*- coding: utf-8 -*-
# ============================================================================
# PROPIEDAD INTELECTUAL Y LICENCIA COMERCIAL CERRADA
# ============================================================================
# Autor Legal y Titular de Derechos: JAVIER ILLAN GONZALEZ
# Organización: ORANGE CREW
# Contacto: ILLANJAVIER9@GMAIL.COM
#
# ADVERTENCIA LEGAL (MÉXICO Y GLOBAL):
# Este código fuente y su arquitectura son propiedad intelectual exclusiva de
# JAVIER ILLAN GONZALEZ. Queda estrictamente prohibida su reproducción,
# distribución, modificación, ingeniería inversa, copia o uso comercial sin la
# autorización expresa y por escrito del autor. Obra protegida conforme a la
# Ley Federal del Derecho de Autor y tratados internacionales aplicables.
# ============================================================================
import os
import time
import secrets
import threading
from flask import (
    Blueprint,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
    current_app,
)
from werkzeug.exceptions import abort
from werkzeug.security import check_password_hash, generate_password_hash
try:
    from authlib.integrations.flask_client import OAuth
    oauth = OAuth()
except ImportError:
    class _DummyOAuth:
        def init_app(self, app): pass
        def create_client(self, name): return None
        def register(self, **kw): pass
    oauth = _DummyOAuth()

from auth import obtener_empresas_del_usuario
from db import get_connection, hay_usuarios
import usuarios_repo
auth_bp = Blueprint("auth", __name__)

# --- Control de intentos de inicio de sesión (Rate Limiting en memoria) ---
MAX_INTENTOS_LOGIN = 5
VENTANA_BLOQUEO_SEGUNDOS = 300  # 5 minutos
_lock_intentos = threading.Lock()
_intentos_fallidos: dict[str, list[float]] = {}

_GOOGLE_PLACEHOLDERS = frozenset({
    "",
    "mock_client_id",
    "mock_client_secret",
    "REEMPLAZAR_CON_NUEVO_GOOGLE_CLIENT_SECRET_ROTADO",
    "TU_GOOGLE_CLIENT_ID",
    "TU_GOOGLE_CLIENT_SECRET",
})

def _clave_intento(ip: str, usuario: str) -> str:
    return f"{(ip or '0.0.0.0').strip()}|{(usuario or '').strip().lower()}"

def esta_bloqueado_login(ip: str, usuario: str) -> bool:
    ahora = time.time()
    clave = _clave_intento(ip, usuario)
    with _lock_intentos:
        marcas = [t for t in _intentos_fallidos.get(clave, []) if (ahora - t) < VENTANA_BLOQUEO_SEGUNDOS]
        if marcas:
            _intentos_fallidos[clave] = marcas
        else:
            _intentos_fallidos.pop(clave, None)
        return len(marcas) >= MAX_INTENTOS_LOGIN

def registrar_intento_fallido(ip: str, usuario: str) -> int:
    ahora = time.time()
    clave = _clave_intento(ip, usuario)
    with _lock_intentos:
        marcas = [t for t in _intentos_fallidos.get(clave, []) if (ahora - t) < VENTANA_BLOQUEO_SEGUNDOS]
        marcas.append(ahora)
        _intentos_fallidos[clave] = marcas
        return len(marcas)

def limpiar_intentos_login(ip: str, usuario: str = None) -> None:
    with _lock_intentos:
        if usuario is None and ip is None:
            _intentos_fallidos.clear()
            return
        clave = _clave_intento(ip, usuario or "")
        _intentos_fallidos.pop(clave, None)

def _regenerar_sesion_autenticada(usuario_row) -> None:
    """Limpia cualquier estado previo de la sesión para evitar Session Fixation y asigna nuevo token CSRF."""
    session.clear()
    session.permanent = True
    session["usuario_id"] = usuario_row["id"]
    session["rol_global"] = usuario_row["rol_global"]
    session["organizacion_id"] = usuario_row["organizacion_id"]
    session["_csrf_token"] = secrets.token_hex(32)

def _es_peticion_setup_autorizada() -> bool:
    """
    Protege /setup cuando aún no existen usuarios:
    - Si se definió ORANGE_SETUP_TOKEN en el entorno, exige que coincida con
      el query param `token`, el campo de formulario `setup_token` o el header `X-Setup-Token`.
    - Si no se definió ORANGE_SETUP_TOKEN, solo permite el acceso desde loopback local.
    """
    setup_token_env = (os.environ.get("ORANGE_SETUP_TOKEN") or "").strip()
    if setup_token_env:
        token_recibido = (
            request.args.get("token")
            or request.form.get("setup_token")
            or request.headers.get("X-Setup-Token")
            or session.get("_setup_token_ok")
            or ""
        )
        if secrets.compare_digest(str(setup_token_env), str(token_recibido)):
            session["_setup_token_ok"] = setup_token_env
            return True
        return False

    ip_remota = (request.remote_addr or "").strip()
    return ip_remota in ("127.0.0.1", "::1", "localhost")

def _google_oauth_configurado() -> bool:
    cid = (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
    csec = (os.environ.get("GOOGLE_CLIENT_SECRET") or "").strip()
    return cid not in _GOOGLE_PLACEHOLDERS and csec not in _GOOGLE_PLACEHOLDERS

def _obtener_cliente_google():
    if not _google_oauth_configurado():
        return None
    oauth.init_app(current_app)
    cliente = oauth.create_client("google")
    if not cliente:
        oauth.register(
            name="google",
            client_id=os.environ.get("GOOGLE_CLIENT_ID").strip(),
            client_secret=os.environ.get("GOOGLE_CLIENT_SECRET").strip(),
            server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
            client_kwargs={"scope": "openid email profile"},
        )
        cliente = oauth.create_client("google")
    return cliente

# --- Configuración inicial (primer arranque) ---

@auth_bp.route("/setup", methods=["GET", "POST"])
def setup():
    if hay_usuarios():
        return redirect(url_for("auth.login"))

    if not _es_peticion_setup_autorizada():
        abort(
            403,
            description=(
                "El asistente inicial (/setup) solo está permitido desde localhost "
                "o proporcionando el token de instalación (ORANGE_SETUP_TOKEN)."
            ),
        )

    if request.method == "POST":
        nombre_org = request.form["organizacion"].strip()
        nombre_usuario = request.form["nombre"].strip()
        usuario = request.form["usuario"].strip().lower()
        correo = request.form.get("correo", "").strip().lower() or None
        password = request.form["password"]
        nombre_empresa = request.form["empresa"].strip()
        rfc_empresa = request.form.get("rfc_empresa", "").strip().upper()

        if not usuario:
            flash("Escribe un nombre de usuario.", "error")
            return render_template("setup.html")
        if not usuarios_repo.usuario_disponible(usuario):
            flash(f"El usuario '{usuario}' ya está en uso, elige otro.", "error")
            return render_template("setup.html")

        with get_connection() as con:
            cur = con.cursor()
            cur.execute("INSERT INTO organizaciones (nombre) VALUES (?)", (nombre_org,))
            org_id = cur.lastrowid

            cur.execute(
                """INSERT INTO usuarios (organizacion_id, nombre, usuario, correo, password_hash, rol_global)
                   VALUES (?, ?, ?, ?, ?, 'superadmin')""",
                (org_id, nombre_usuario, usuario, correo, generate_password_hash(password)),
            )
            usuario_id = cur.lastrowid

            cur.execute(
                "INSERT INTO empresas (organizacion_id, nombre, rfc) VALUES (?, ?, ?)",
                (org_id, nombre_empresa, rfc_empresa),
            )
            empresa_id = cur.lastrowid

            cur.execute(
                "INSERT INTO usuario_empresa (usuario_id, empresa_id, rol) VALUES (?, ?, 'admin')",
                (usuario_id, empresa_id),
            )
            usuario_row = cur.execute("SELECT * FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()

        _regenerar_sesion_autenticada(usuario_row)
        flash("Cuenta y empresa creadas correctamente.", "exito")
        return redirect(url_for("empresas.dashboard", empresa_id=empresa_id))

    return render_template("setup.html")

# --- Autenticación ---

@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if not hay_usuarios():
        return redirect(url_for("auth.setup"))

    if request.method == "POST":
        nombre_usuario = (request.form.get("usuario") or "").strip().lower()
        password = request.form.get("password") or ""
        ip_cliente = request.remote_addr or "0.0.0.0"

        if esta_bloqueado_login(ip_cliente, nombre_usuario):
            flash(
                "Demasiados intentos fallidos de inicio de sesión. Espera 5 minutos antes de volver a intentarlo.",
                "error",
            )
            return render_template("login.html"), 429

        with get_connection() as con:
            usuario = con.execute(
                "SELECT * FROM usuarios WHERE usuario = ? AND activo = 1",
                (nombre_usuario,),
            ).fetchone()

        if usuario and check_password_hash(usuario["password_hash"], password):
            limpiar_intentos_login(ip_cliente, nombre_usuario)
            _regenerar_sesion_autenticada(usuario)
            empresas = obtener_empresas_del_usuario(usuario["id"])
            if len(empresas) == 1:
                return redirect(url_for("empresas.dashboard", empresa_id=empresas[0]["id"]))
            return redirect(url_for("empresas.selector_empresas"))

        intentos = registrar_intento_fallido(ip_cliente, nombre_usuario)
        if intentos >= MAX_INTENTOS_LOGIN:
            flash(
                "Demasiados intentos fallidos de inicio de sesión. Cuenta temporalmente bloqueada por 5 minutos.",
                "error",
            )
            return render_template("login.html"), 429

        flash("Usuario o contraseña incorrectos.", "error")

    return render_template("login.html")

@auth_bp.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("auth.login"))

@auth_bp.route("/suscripcion-vencida")
def suscripcion_vencida():
    if "usuario_id" not in session:
        return redirect(url_for("auth.login"))
    return render_template("suscripcion_vencida.html"), 402

@auth_bp.route("/login/google")
def login_google():
    cliente = _obtener_cliente_google()
    if not cliente:
        flash("El inicio de sesión con Google OAuth no está configurado en el servidor.", "error")
        return redirect(url_for("auth.login"))
    redirect_uri = url_for("auth.google_autorizado", _external=True)
    return cliente.authorize_redirect(redirect_uri)

@auth_bp.route("/login/google/autorizado")
def google_autorizado():
    cliente = _obtener_cliente_google()
    if not cliente:
        flash("El inicio de sesión con Google OAuth no está configurado en el servidor.", "error")
        return redirect(url_for("auth.login"))

    try:
        token = cliente.authorize_access_token()
        user_info = token.get("userinfo") if isinstance(token, dict) else None
        if not user_info:
            flash("No se pudo obtener información de Google.", "error")
            return redirect(url_for("auth.login"))

        if user_info.get("email_verified") is not True:
            flash("Tu cuenta de Google no tiene un correo verificado.", "error")
            return redirect(url_for("auth.login"))

        correo = (user_info.get("email") or "").strip().lower()
        if not correo:
            flash("Google no devolvió una dirección de correo válida.", "error")
            return redirect(url_for("auth.login"))

        with get_connection() as con:
            usuario = con.execute(
                "SELECT * FROM usuarios WHERE correo = ? OR usuario = ? LIMIT 1",
                (correo, correo),
            ).fetchone()

        if usuario:
            if usuario["activo"] != 1:
                flash("Tu cuenta está desactivada. Contacta a tu administrador.", "error")
                return redirect(url_for("auth.login"))

            _regenerar_sesion_autenticada(usuario)
            return redirect(url_for("empresas.selector_empresas"))

        flash(
            f"El correo {correo} no está registrado en ningún despacho. "
            f"Pide a tu administrador que te invite primero.",
            "error",
        )
        return redirect(url_for("auth.login"))

    except Exception as e:
        flash(f"Error al verificar la identidad con Google: {str(e)}", "error")
        return redirect(url_for("auth.login"))
