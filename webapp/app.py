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

import sys

import tempfile

import uuid

import shutil

from itertools import zip_longest

from flask import Flask, flash, g, redirect, render_template, request, session, url_for, send_file

from werkzeug.security import check_password_hash, generate_password_hash

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "app", "core"))

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "app", "importers"))

from excel_importer import (

    importar_excel, ConfiguracionImportacion, FORMATO_COLUMNAS_SEPARADAS,

    FORMATO_COLUMNA_CON_SIGNO, FORMATO_VALOR_ABSOLUTO_Y_TIPO,

    listar_hojas, vista_previa, vista_previa_cruda, detectar_fila_encabezado,

)

from rules_repository import RepositorioReglas

from db import inicializar_db, hay_usuarios, get_connection, ErrorConexionBD

from auth import (
    login_required,
    empresa_requerida,
    obtener_empresas_del_usuario,
    obtener_rol_en_empresa,
    obtener_token_csrf_request,
)

import movimientos_repo

import cfdi_repo

import catalogo_repo

import bancos_repo

import marcado_repo

import usuarios_repo

import reglas_generales_repo

from asignacion_rapida import construir_plantilla_simple, validar_cuentas_iva_configuradas

# Vistas previas de importación pendientes de confirmar: token -> {archivos, banco_id}.
# En memoria del proceso (suficiente para un servidor Flask de un solo
# worker, como el que levanta iniciar_orange_poliza.bat). Si en el futuro
# se corre con varios workers, esto debe pasar a la base de datos o a Redis.

def _mover_archivo_con_reintentos(origen, destino, intentos=5, espera_segundos=0.4):
    """
    En Windows, un antivirus o el propio SO puede tener el archivo
    recién subido bloqueado por un instante (WinError 32). Reintenta
    con una pequeña espera y, si sigue sin poder mover, copia y borra
    el original por separado — así el import nunca truena solo por
    esto, aunque el archivo temporal se quede huérfano en el peor caso
    (ya se limpia después con shutil.rmtree al final de la importación).
    """
    import time
    ultimo_error = None
    for intento in range(intentos):
        try:
            shutil.move(origen, destino)
            return
        except (PermissionError, OSError) as e:
            ultimo_error = e
            time.sleep(espera_segundos)

    # Último recurso: copiar y borrar por separado. Si el borrado falla,
    # no importa: ya tenemos el destino, y la carpeta temporal se limpia
    # (con ignore_errors=True) al final de importar_confirmar.
    try:
        shutil.copy2(origen, destino)
    except Exception:
        raise ultimo_error
    try:
        os.remove(origen)
    except OSError:
        pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
SECRET_KEY_PATH = os.path.join(BASE_DIR, "secret.key")

app = Flask(__name__)

from routes.auth_routes import auth_bp
from routes.empresas_routes import empresas_bp
from routes.importacion_routes import importacion_bp
from routes.pagos_routes import pagos_bp
from routes.reglas_routes import reglas_bp
from routes.api_routes import api_bp
from routes.polizas_routes import polizas_bp
from routes.saas_routes import saas_bp
from routes.configuracion_routes import configuracion_bp
from routes.billing_routes import billing_bp
from routes.despacho_routes import despacho_bp

app.register_blueprint(auth_bp)
app.register_blueprint(empresas_bp)
app.register_blueprint(importacion_bp)
app.register_blueprint(pagos_bp)
app.register_blueprint(reglas_bp)
app.register_blueprint(api_bp)
app.register_blueprint(polizas_bp)
app.register_blueprint(saas_bp)
app.register_blueprint(configuracion_bp)
app.register_blueprint(billing_bp, url_prefix='/billing')

@app.errorhandler(ErrorConexionBD)
def _manejar_error_conexion_bd(error):
    """Si PostgreSQL se cae o se vuelve inalcanzable a media sesión (no
    solo al arrancar), esto evita que la persona vea un traceback en
    vez de un mensaje entendible."""
    return str(error), 503

def _obtener_secret_key():
    clave_env = (os.environ.get("SECRET_KEY") or "").strip()
    if clave_env and clave_env not in ("TU_CLAVE_SECRETA_AQUI", "clave_secreta_en_produccion_debe_cambiarse"):
        return clave_env.encode("utf-8")

    if os.path.exists(SECRET_KEY_PATH):
        return open(SECRET_KEY_PATH, "rb").read()

    clave = os.urandom(32)
    try:
        with open(SECRET_KEY_PATH, "wb") as f:
            f.write(clave)
    except OSError:
        pass
    return clave

import secrets
import datetime

app.secret_key = _obtener_secret_key()
app.config["MAX_CONTENT_LENGTH"] = int(os.environ.get("MAX_CONTENT_LENGTH", 32 * 1024 * 1024))
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = os.environ.get("SESSION_COOKIE_SAMESITE", "Lax")
app.config["SESSION_COOKIE_SECURE"] = (os.environ.get("SESSION_COOKIE_SECURE", "0").strip() == "1")
app.config["PERMANENT_SESSION_LIFETIME"] = datetime.timedelta(hours=12)

def generate_csrf_token():
    if "_csrf_token" not in session:
        session["_csrf_token"] = secrets.token_hex(32)
    return session["_csrf_token"]

@app.context_processor
def inject_csrf_token():
    return dict(csrf_token=generate_csrf_token)

_CSRF_EXEMPT_ENDPOINTS = frozenset({
    "auth.login",
    "auth.setup",
    "pagos.stripe_webhook",
    "api.classify_movements",
})

_SUSCRIPCION_EXEMPT_ENDPOINTS = frozenset({
    "static",
    "auth.login",
    "auth.logout",
    "auth.setup",
    "auth.login_google",
    "auth.google_autorizado",
    "auth.google_login",
    "auth.google_authorize",
    "auth.suscripcion_vencida",
    "pagos.crear_checkout",
    "pagos.exito",
    "pagos.stripe_webhook",
    "pagos.suscripcion_vencida",
    "billing.pagar_suscripcion",
    "billing.mock_checkout",
    "billing.pago_exitoso",
})

@app.before_request
def csrf_protect_global():
    if request.method in ("POST", "PUT", "DELETE", "PATCH"):
        if request.endpoint in _CSRF_EXEMPT_ENDPOINTS:
            return
        token_sesion = session.get("_csrf_token")
        token_req = obtener_token_csrf_request()
        if not token_sesion or not token_req or not secrets.compare_digest(str(token_sesion), str(token_req)):
            from werkzeug.exceptions import abort
            abort(403, description="Token CSRF inválido o expirado. Vuelve atrás, recarga la página e inténtalo de nuevo.")

@app.before_request
def cargar_usuario():
    g.usuario = None
    if "usuario_id" in session:
        with get_connection() as con:
            g.usuario = con.execute(
                """SELECT u.*,
                          o.nombre AS organizacion_nombre,
                          o.estado_suscripcion AS organizacion_estado_suscripcion,
                          o.fecha_vencimiento AS organizacion_fecha_vencimiento
                   FROM usuarios u
                   JOIN organizaciones o ON o.id = u.organizacion_id
                   WHERE u.id = ?""",
                (session["usuario_id"],),
            ).fetchone()

@app.before_request
def verificar_suscripcion_activa():
    if request.endpoint in _SUSCRIPCION_EXEMPT_ENDPOINTS:
        return
    usuario = g.get("usuario")
    if not usuario:
        return
    if usuario.get("rol_global") == "superadmin":
        return

    estado = str(usuario.get("organizacion_estado_suscripcion") or "activa").strip().lower()
    fecha_venc = usuario.get("organizacion_fecha_vencimiento")

    if fecha_venc and estado in ("activa", "trial"):
        try:
            dt_venc = datetime.datetime.fromisoformat(str(fecha_venc))
            if dt_venc < datetime.datetime.now():
                estado = "vencida"
                with get_connection() as con:
                    con.execute(
                        "UPDATE organizaciones SET estado_suscripcion = 'vencida' WHERE id = ?",
                        (usuario["organizacion_id"],),
                    )
        except ValueError:
            pass

    if estado not in ("activa", "trial"):
        if request.is_json or request.path.startswith("/api/"):
            return {"exito": False, "mensaje": "La suscripción del despacho está vencida o suspendida."}, 402
        return render_template("suscripcion_vencida.html"), 402

@app.context_processor
def inyectar_globals():
    empresas = []
    if g.get("usuario"):
        empresas = obtener_empresas_del_usuario(g.usuario["id"])
    return {"usuario_actual": g.get("usuario"), "empresas_usuario": empresas}

import logging
import traceback
from werkzeug.exceptions import HTTPException

@app.errorhandler(413)
def request_entity_too_large(e):
    return render_template(
        "error.html",
        titulo="Archivo demasiado grande",
        mensaje="El archivo o conjunto de archivos excede el tamaño máximo permitido (32 MB).",
        detalle=str(e),
    ), 413

@app.errorhandler(404)
def page_not_found(e):
    return render_template("error.html", titulo="Página no encontrada", mensaje="La ruta solicitada no existe.", detalle=str(e)), 404

@app.errorhandler(500)
@app.errorhandler(Exception)
def internal_server_error(e):
    if isinstance(e, HTTPException):
        return e
    if isinstance(e, ErrorConexionBD):
        return _manejar_error_conexion_bd(e)
    logging.error("Excepción no controlada: %s", traceback.format_exc())
    return render_template("error.html", titulo="Error interno del servidor", mensaje="Ocurrió un problema procesando la solicitud.", detalle=str(e)), 500

if __name__ == '__main__':
    try:
        inicializar_db()
    except ErrorConexionBD as e:
        print("ERROR CRITICO: No se pudo conectar a la base de datos al arrancar.")
        print(str(e))
        import sys
        sys.exit(1)

    modo_debug = (os.environ.get("FLASK_DEBUG", "0").strip() == "1")
    host_bind = (os.environ.get("FLASK_RUN_HOST") or "127.0.0.1").strip()
    port_bind = int(os.environ.get("FLASK_RUN_PORT") or "5000")
    app.run(debug=modo_debug, host=host_bind, port=port_bind)

