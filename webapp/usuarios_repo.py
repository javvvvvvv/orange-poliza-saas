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
from werkzeug.security import generate_password_hash

from auth import ROLES_DESPACHO_PERMITIDOS, ROLES_EMPRESA_PERMITIDOS
from db import get_connection

def listar_usuarios_de_empresa(empresa_id):
    with get_connection() as con:
        filas = con.execute(
            """SELECT u.id, u.nombre, u.usuario, u.correo, ue.rol
               FROM usuarios u JOIN usuario_empresa ue ON ue.usuario_id = u.id
               WHERE ue.empresa_id = ? ORDER BY u.nombre""",
            (empresa_id,),
        ).fetchall()
        return filas

def buscar_usuario_por_usuario(usuario):
    """Login es global por nombre de usuario (no depende de la empresa
    ni de la organización): dos organizaciones no pueden compartir el
    mismo nombre de usuario, así que basta buscarlo directo."""
    with get_connection() as con:
        fila = con.execute(
            "SELECT * FROM usuarios WHERE usuario = ?",
            (usuario.strip().lower(),),
        ).fetchone()
        return fila

def buscar_usuario_por_usuario_en_organizacion(organizacion_id, usuario):
    with get_connection() as con:
        fila = con.execute(
            "SELECT * FROM usuarios WHERE organizacion_id = ? AND usuario = ?",
            (organizacion_id, usuario.strip().lower()),
        ).fetchone()
        return fila

def usuario_disponible(usuario):
    return buscar_usuario_por_usuario(usuario) is None

def crear_usuario(organizacion_id, nombre, usuario, password, correo=None, rol_global="usuario"):
    rol_norm = (rol_global or "usuario").strip().lower()
    if rol_norm not in ROLES_DESPACHO_PERMITIDOS:
        raise ValueError(f"Rol global no permitido: {rol_global!r}")
    usuario_norm = usuario.strip().lower()
    correo_norm = (correo or "").strip().lower() or None
    with get_connection() as con:
        cur = con.cursor()
        cur.execute(
            """INSERT INTO usuarios (organizacion_id, nombre, usuario, correo, password_hash, rol_global)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (organizacion_id, nombre, usuario_norm, correo_norm, generate_password_hash(password), rol_norm),
        )
        usuario_id = cur.lastrowid
        con.commit()
        return usuario_id

def asignar_a_empresa(usuario_id, empresa_id, rol):
    rol_norm = (rol or "editor").strip().lower()
    if rol_norm not in ROLES_EMPRESA_PERMITIDOS:
        raise ValueError(f"Rol de empresa no permitido: {rol!r}")
    with get_connection() as con:
        # Verificar que usuario y empresa pertenezcan a la misma organizacion
        misma_org = con.execute(
            """SELECT 1 FROM usuarios u
               JOIN empresas e ON e.organizacion_id = u.organizacion_id
               WHERE u.id = ? AND e.id = ?""",
            (usuario_id, empresa_id),
        ).fetchone()
        if not misma_org:
            raise PermissionError("El usuario y la empresa no pertenecen a la misma organización.")
        con.execute(
            """INSERT INTO usuario_empresa (usuario_id, empresa_id, rol) VALUES (?, ?, ?)
               ON CONFLICT(usuario_id, empresa_id) DO UPDATE SET rol = excluded.rol""",
            (usuario_id, empresa_id, rol_norm),
        )
        con.commit()

def quitar_de_empresa(usuario_id, empresa_id):
    with get_connection() as con:
        cur = con.execute(
            "DELETE FROM usuario_empresa WHERE usuario_id = ? AND empresa_id = ?",
            (usuario_id, empresa_id),
        )
        con.commit()
        return cur.rowcount > 0

def obtener_organizacion_de_empresa(empresa_id):
    with get_connection() as con:
        fila = con.execute("SELECT organizacion_id FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
        return fila["organizacion_id"] if fila else None

