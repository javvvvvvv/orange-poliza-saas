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
from functools import wraps

from flask import g, redirect, request, session, url_for, flash

from db import get_connection

def login_required(vista):
    @wraps(vista)
    def envoltura(*args, **kwargs):
        if "usuario_id" not in session or not getattr(g, "usuario", None):
            session.clear()
            return redirect(url_for("auth.login"))
        return vista(*args, **kwargs)
    return envoltura

def empresa_requerida(vista):
    """Verifica que el usuario tenga acceso a la empresa de la URL y, de
    paso, bloquea cualquier método que modifique datos (POST/PUT/DELETE)
    si su rol en esa empresa es 'lector' — sin esto, 'solo lectura'
    no significaba nada: cualquier rol podía borrar el catálogo,
    cambiar la config de IVA, eliminar bancos, etc."""
    @wraps(vista)
    def envoltura(empresa_id, *args, **kwargs):
        rol = obtener_rol_en_empresa(session["usuario_id"], empresa_id)
        if rol is None:
            return "No tienes acceso a esta empresa.", 403
        if rol == "lector" and request.method != "GET":
            return "Tu rol es de solo lectura en esta empresa.", 403
        g.empresa_id = empresa_id
        g.rol_empresa = rol
        return vista(empresa_id, *args, **kwargs)
    return envoltura

def obtener_empresas_del_usuario(usuario_id):
    with get_connection() as con:
        usr = con.execute("SELECT organizacion_id, rol_global FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
        if not usr:
            return []

        org_id = usr["organizacion_id"]
        rol = usr["rol_global"]

        if rol == "superadmin":
            return con.execute(
                "SELECT id, nombre, rfc, tasa_iva, 'superadmin' as rol FROM empresas WHERE activa = 1 ORDER BY nombre"
            ).fetchall()
        elif rol in ("admin", "gerente"):
            return con.execute(
                "SELECT id, nombre, rfc, tasa_iva, ? as rol FROM empresas WHERE organizacion_id = ? AND activa = 1 ORDER BY nombre",
                (rol, org_id)
            ).fetchall()
        else:
            return con.execute(
                """SELECT e.id, e.nombre, e.rfc, e.tasa_iva, ue.rol as rol
                   FROM empresas e
                   JOIN usuario_empresa ue ON ue.empresa_id = e.id
                   WHERE ue.usuario_id = ? AND e.organizacion_id = ? AND e.activa = 1
                   ORDER BY e.nombre""",
                (usuario_id, org_id)
            ).fetchall()

ROLES_DESPACHO_PERMITIDOS = (
    "admin",
    "gerente",
    "contador",
    "analista",
    "usuario",
    "capturista",
    "lector",
)

JERARQUIA_ROL_GLOBAL = {
    "superadmin": 100,
    "admin": 80,
    "gerente": 60,
    "contador": 40,
    "analista": 40,
    "usuario": 30,
    "capturista": 20,
    "lector": 10,
}

ROLES_EMPRESA_PERMITIDOS = (
    "admin",
    "editor",
    "capturista",
    "lector",
)

def validar_rol_despacho_asignable(rol_solicitado: str, rol_asignador: str) -> tuple[bool, str]:
    """Valida contra lista blanca que el rol de despacho sea permitido (nunca
    'superadmin') y que quien lo asigna no otorgue un rol superior al suyo."""
    rol_norm = (rol_solicitado or "").strip().lower()
    if not rol_norm or rol_norm not in ROLES_DESPACHO_PERMITIDOS:
        return False, f"Rol '{rol_solicitado}' no permitido. Roles válidos: {', '.join(ROLES_DESPACHO_PERMITIDOS)}."
    nivel_asignador = JERARQUIA_ROL_GLOBAL.get((rol_asignador or "").strip().lower(), 0)
    nivel_solicitado = JERARQUIA_ROL_GLOBAL.get(rol_norm, 0)
    if nivel_solicitado > nivel_asignador:
        return False, "No puedes asignar un rol con privilegios superiores la tuyo."
    return True, rol_norm

def validar_rol_empresa_asignable(rol_solicitado: str) -> tuple[bool, str]:
    """Valida contra lista blanca el rol dentro de una empresa."""
    rol_norm = (rol_solicitado or "").strip().lower()
    if not rol_norm or rol_norm not in ROLES_EMPRESA_PERMITIDOS:
        return False, f"Rol de empresa '{rol_solicitado}' no permitido. Roles válidos: {', '.join(ROLES_EMPRESA_PERMITIDOS)}."
    return True, rol_norm

def obtener_rol_en_empresa(usuario_id, empresa_id):
    with get_connection() as con:
        usr = con.execute("SELECT organizacion_id, rol_global FROM usuarios WHERE id = ?", (usuario_id,)).fetchone()
        if not usr:
            return None

        if usr["rol_global"] == "superadmin":
            emp = con.execute("SELECT id FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
            return "superadmin" if emp else None

        emp = con.execute(
            "SELECT id FROM empresas WHERE id = ? AND organizacion_id = ?",
            (empresa_id, usr["organizacion_id"]),
        ).fetchone()
        if not emp:
            return None

        fila = con.execute(
            "SELECT rol FROM usuario_empresa WHERE usuario_id = ? AND empresa_id = ?",
            (usuario_id, empresa_id),
        ).fetchone()
        if fila:
            return fila["rol"]

        # Si no está explícitamente asignado: solo los admin o gerentes del despacho conservan acceso implícito
        if usr["rol_global"] in ("admin", "gerente"):
            return usr["rol_global"]

        # Usuarios regulares no asignados a esta empresa no tienen acceso
        return None

import secrets
from werkzeug.exceptions import abort

def obtener_token_csrf_request():
    """Obtiene el token CSRF enviado en el formulario, en los headers
    (`X-CSRFToken` o `X-CSRF-Token`) o en el payload JSON (`csrf_token`)."""
    token = (
        request.form.get("csrf_token")
        or request.headers.get("X-CSRFToken")
        or request.headers.get("X-CSRF-Token")
    )
    if not token and request.is_json:
        datos = request.get_json(silent=True)
        if isinstance(datos, dict):
            token = datos.get("csrf_token")
    return token

def csrf_protect(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if request.method in ("POST", "PUT", "DELETE", "PATCH"):
            token_sesion = session.get("_csrf_token")
            token_req = obtener_token_csrf_request()
            if not token_sesion or not token_req or not secrets.compare_digest(str(token_sesion), str(token_req)):
                abort(403, description="Token CSRF inválido o expirado. Vuelve atrás, recarga la página e inténtalo de nuevo.")
        return f(*args, **kwargs)
    return decorated_function

def gerente_requerido(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if g.usuario.get("rol_global") not in ("superadmin", "gerente", "admin"):
            flash("No tienes permisos de gerente para realizar esta acción.", "error")
            return redirect(url_for("empresas.selector_empresas"))
        return f(*args, **kwargs)
    return decorated_function

def log_audit(organizacion_id, usuario_id, accion, detalle="", con=None):
    from db import get_connection
    cerrar = con is None
    if cerrar:
        con = get_connection()
    try:
        con.execute("INSERT INTO auditoria_despacho (organizacion_id, usuario_id, accion, detalle) VALUES (?, ?, ?, ?)",
                    (organizacion_id, usuario_id, accion, detalle))
        con.commit()
    finally:
        if cerrar:
            con.close()

def superadmin_requerido(vista):
    @wraps(vista)
    def envoltura(*args, **kwargs):
        if "usuario_id" not in session:
            return redirect(url_for("auth.login"))
        with get_connection() as con:
            usr = con.execute("SELECT rol_global FROM usuarios WHERE id = ?", (session["usuario_id"],)).fetchone()
        if not usr or usr["rol_global"] != "superadmin":
            from flask import flash
            flash("Acceso denegado: Se requiere permiso de Super Administrador SaaS.", "error")
            return redirect(url_for("empresas.selector_empresas"))
        return vista(*args, **kwargs)
    return envoltura

def admin_despacho_requerido(vista):
    @wraps(vista)
    def envoltura(*args, **kwargs):
        if "usuario_id" not in session:
            return redirect(url_for("auth.login"))
        with get_connection() as con:
            usr = con.execute("SELECT rol_global FROM usuarios WHERE id = ?", (session["usuario_id"],)).fetchone()
        if not usr or usr["rol_global"] not in ("superadmin", "admin"):
            from flask import flash
            flash("Acceso denegado: Se requiere permiso de Administrador de Despacho.", "error")
            return redirect(url_for("empresas.selector_empresas"))
        return vista(*args, **kwargs)
    return envoltura

