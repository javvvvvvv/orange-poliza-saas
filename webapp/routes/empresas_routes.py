# ============================================================================
# PROPIEDAD INTELECTUAL Y LICENCIA COMERCIAL CERRADA
# ============================================================================
# Autor Legal y Titular de Derechos: JAVIER ILLAN GONZALEZ
# Organización: ORANGE CREW
# Contacto: ILLANJAVIER9@GMAIL.COM
# ============================================================================
import os
import uuid
import tempfile
import shutil
import socket
import ipaddress
from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for, send_file, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash

from auth import (
    login_required,
    gerente_requerido,
    empresa_requerida,
    obtener_empresas_del_usuario,
    csrf_protect,
    validar_rol_despacho_asignable,
)
from db import get_connection, hay_usuarios
from crypto_utils import cifrar_texto, descifrar_texto
import usuarios_repo
import bancos_repo
import catalogo_repo
import cfdi_repo
import marcado_repo
import movimientos_repo
import reglas_generales_repo
from rules_repository import RepositorioReglas

from excel_importer import (
    importar_excel, ConfiguracionImportacion, FORMATO_COLUMNAS_SEPARADAS,
    FORMATO_COLUMNA_CON_SIGNO, FORMATO_VALOR_ABSOLUTO_Y_TIPO,
    listar_hojas, vista_previa, vista_previa_cruda, detectar_fila_encabezado
)
from asignacion_rapida import construir_plantilla_simple, validar_cuentas_iva_configuradas

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOADS_DIR = os.path.join(BASE_DIR, '..', "uploads")

def _mover_archivo_con_reintentos(origen, destino, intentos=5, espera_segundos=0.4):
    import time
    ultimo_error = None
    for intento in range(intentos):
        try:
            shutil.move(origen, destino)
            return
        except (PermissionError, OSError) as e:
            ultimo_error = e
            time.sleep(espera_segundos)
    try:
        shutil.copy2(origen, destino)
    except Exception:
        raise ultimo_error
    try:
        os.remove(origen)
    except OSError:
        pass

empresas_bp = Blueprint('empresas', __name__)

@empresas_bp.route("/")
@login_required
def index():
    empresas = obtener_empresas_del_usuario(g.usuario["id"])
    if len(empresas) == 1:
        return redirect(url_for("empresas.dashboard", empresa_id=empresas[0]["id"]))
    return redirect(url_for("empresas.selector_empresas"))

@empresas_bp.route("/empresas")
@login_required
def selector_empresas():
    empresas = obtener_empresas_del_usuario(g.usuario["id"])
    return render_template("selector_empresas.html", empresas=empresas)

_BLOCKED_HOSTNAMES = frozenset({
    "metadata.google.internal",
    "169.254.169.254",
    "100.100.100.200",
    "instance-data",
})

_BLOCKED_PORTS = frozenset({
    22, 23, 25, 80, 443, 3000, 3001, 3005, 5005, 5432, 5433, 6379, 8000, 9090, 9121
})

def _validar_servidor_sql_seguro(servidor: str) -> tuple[bool, str]:
    if not servidor or not servidor.strip():
        return False, "El servidor no puede estar vacío."
    
    srv = servidor.strip()
    host_part = srv
    port_part = None
    
    if "\\" in host_part:
        host_part = host_part.split("\\")[0]
    if "," in host_part:
        partes = host_part.split(",")
        host_part = partes[0]
        if len(partes) > 1 and partes[1].isdigit():
            port_part = int(partes[1])
    elif ":" in host_part:
        partes = host_part.split(":")
        host_part = partes[0]
        if len(partes) > 1 and partes[1].isdigit():
            port_part = int(partes[1])
            
    if port_part and port_part in _BLOCKED_PORTS:
        return False, f"El puerto {port_part} está bloqueado por motivos de seguridad."
        
    host_limpio = host_part.strip().lower()
    if host_limpio in _BLOCKED_HOSTNAMES:
        return False, "Host no permitido por políticas de seguridad de red."
        
    try:
        ip_info = socket.getaddrinfo(host_limpio, None)
        for _, _, _, _, sockaddr in ip_info:
            ip_str = sockaddr[0]
            ip_obj = ipaddress.ip_address(ip_str)
            if ip_obj.is_link_local:
                return False, "Dirección de enlace local (link-local) no permitida."
    except Exception:
        pass
        
    return True, ""

@empresas_bp.route("/empresas/<int:empresa_id>/ajustes/probar_sql", methods=["POST"])
@login_required
@gerente_requerido
@empresa_requerida
@csrf_protect
def probar_sql(empresa_id):
    datos = request.get_json() or {}
    servidor = (datos.get("servidor") or "").strip()
    usuario = (datos.get("usuario") or "").strip()
    password = datos.get("password", "")
    
    if not servidor or not usuario:
        return {"exito": False, "mensaje": "Faltan datos de conexion."}
    
    es_seguro, motivo_inseguro = _validar_servidor_sql_seguro(servidor)
    if not es_seguro:
        return {"exito": False, "mensaje": motivo_inseguro}

    if not password:
        with get_connection() as con:
            emp = con.execute("SELECT sql_password FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
            if emp and emp["sql_password"]:
                password = descifrar_texto(emp["sql_password"])
    
    try:
        import pymssql
        conn = pymssql.connect(server=servidor, user=usuario, password=password, database="master", login_timeout=3, timeout=3)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sys.databases WHERE name LIKE 'ct%' ORDER BY name")
        dbs = [row[0] for row in cursor.fetchall()]
        conn.close()
        return {"exito": True, "dbs": dbs}
    except ImportError:
        return {"exito": False, "mensaje": "El controlador pymssql no está disponible en este entorno."}
    except Exception as e:
        return {"exito": False, "mensaje": f"No fue posible conectar con el servidor SQL Server ({type(e).__name__}). Verifique servidor, credenciales y puerto."}

@empresas_bp.route("/empresas/nueva", methods=["GET", "POST"])
@login_required
def empresa_nueva():
    if request.method == "POST":
        nombre = request.form["nombre"].strip()
        if not nombre:
            flash("Escribe el nombre de la empresa.", "error")
            return render_template("empresa_nueva.html")

        with get_connection() as con:
            cur = con.cursor()
            cur.execute(
                "INSERT INTO empresas (organizacion_id, nombre, rfc) VALUES (?, ?, ?)",
                (g.usuario["organizacion_id"], nombre,
                 request.form.get("rfc", "").strip().upper()),
            )
            empresa_id = cur.lastrowid
            cur.execute(
                "INSERT INTO usuario_empresa (usuario_id, empresa_id, rol) VALUES (?, ?, 'admin')",
                (g.usuario["id"], empresa_id),
            )
            con.commit()
            from auth import log_audit
            log_audit(g.usuario["organizacion_id"], g.usuario["id"], "Crear Empresa", f"Se creó la empresa '{nombre}'", con=con)
        flash(f"Empresa '{nombre}' creada.", "exito")
        return redirect(url_for("empresas.dashboard", empresa_id=empresa_id))

    return render_template("empresa_nueva.html")

# --- Dashboard ---

@empresas_bp.route("/empresas/<int:empresa_id>/")
@login_required
@empresa_requerida
def dashboard(empresa_id):
    with get_connection() as con:
        empresa = con.execute("SELECT * FROM empresas WHERE id = ?", (empresa_id,)).fetchone()

        total = con.execute(
            "SELECT COUNT(*) FROM movimientos WHERE empresa_id = ?", (empresa_id,)
        ).fetchone()[0]
        automaticos = con.execute(
            "SELECT COUNT(*) FROM movimientos WHERE empresa_id = ? AND estado_clasificacion = 'automatico'",
            (empresa_id,),
        ).fetchone()[0]
        pendientes = con.execute(
            "SELECT COUNT(*) FROM movimientos WHERE empresa_id = ? AND estado_clasificacion = 'pendiente'",
            (empresa_id,),
        ).fetchone()[0]
        con_poliza = con.execute(
            "SELECT COUNT(*) FROM movimientos WHERE empresa_id = ? AND poliza_id IS NOT NULL",
            (empresa_id,),
        ).fetchone()[0]
        ultimos = con.execute(
            "SELECT * FROM movimientos WHERE empresa_id = ? ORDER BY id DESC LIMIT 10",
            (empresa_id,),
        ).fetchall()
        n_cuentas = con.execute("SELECT COUNT(*) FROM cuentas_catalogo WHERE empresa_id = ?", (empresa_id,)).fetchone()[0]
        n_bancos = con.execute("SELECT COUNT(*) FROM bancos WHERE empresa_id = ?", (empresa_id,)).fetchone()[0]
        n_reglas = con.execute("SELECT COUNT(*) FROM reglas WHERE empresa_id = ? AND activa = 1", (empresa_id,)).fetchone()[0]

        # Analiticas Financieras
        ingresos = con.execute("SELECT SUM(total) FROM movimientos WHERE empresa_id = ? AND tipo = 'ingreso'", (empresa_id,)).fetchone()[0] or 0.0
        egresos = con.execute("SELECT SUM(total) FROM movimientos WHERE empresa_id = ? AND tipo = 'egreso'", (empresa_id,)).fetchone()[0] or 0.0
        
        # Agrupacion por categorias (usando reglas)
        grafica_ingresos = con.execute("""
            SELECT COALESCE(r.nombre, 'Sin Clasificar') as categoria, SUM(m.total) as suma
            FROM movimientos m
            LEFT JOIN reglas r ON r.id = m.regla_id
            WHERE m.empresa_id = ? AND m.tipo = 'ingreso'
            GROUP BY r.nombre
            ORDER BY suma DESC LIMIT 10
        """, (empresa_id,)).fetchall()
        
        grafica_egresos = con.execute("""
            SELECT COALESCE(r.nombre, 'Sin Clasificar') as categoria, SUM(m.total) as suma
            FROM movimientos m
            LEFT JOIN reglas r ON r.id = m.regla_id
            WHERE m.empresa_id = ? AND m.tipo = 'egreso'
            GROUP BY r.nombre
            ORDER BY suma DESC LIMIT 10
        """, (empresa_id,)).fetchall()
    
    # Formatear a diccionarios
    grafica_ingresos = [{"label": g[0], "value": float(g[1])} for g in grafica_ingresos]
    grafica_egresos = [{"label": g[0], "value": float(g[1])} for g in grafica_egresos]

    pct = round((automaticos / total * 100), 1) if total else 0.0

    # Checklist de configuración: guía a la empresa a lo que le falta
    # para que la clasificación automática funcione bien, en vez de que
    # el usuario tenga que adivinar por qué "no está clasificando nada".
    pendientes_config = []
    if n_cuentas == 0:
        pendientes_config.append({
            "texto": "Sube tu catálogo de cuentas (aún no tiene ninguna cuenta cargada).",
            "url": url_for("configuracion.catalogo", empresa_id=empresa_id),
        })
    if n_bancos == 0:
        pendientes_config.append({
            "texto": "Registra al menos un banco (necesario para importar estados de cuenta).",
            "url": url_for("configuracion.configuracion", empresa_id=empresa_id),
        })
    if not empresa["cuenta_iva_acreditable"] or not empresa["cuenta_iva_trasladado"]:
        pendientes_config.append({
            "texto": "Configura las cuentas de IVA (acreditable/trasladado) para que las pólizas con impuestos cuadren solas.",
            "url": url_for("configuracion.configuracion", empresa_id=empresa_id),
        })
    if total > 0 and n_reglas == 0:
        pendientes_config.append({
            "texto": "Todavía no tienes reglas guardadas: clasifica tus primeros movimientos pendientes y el sistema aprenderá para los siguientes.",
            "url": url_for("reglas.pendientes_siguiente", empresa_id=empresa_id),
        })

    return render_template("dashboard.html", ingresos_totales=ingresos, egresos_totales=egresos, flujo=ingresos-egresos, grafica_ingresos=grafica_ingresos, grafica_egresos=grafica_egresos, empresa=empresa, total=total, automaticos=automaticos,
        pendientes=pendientes, con_poliza=con_poliza, pct=pct, ultimos=ultimos,
        pendientes_config=pendientes_config,
    )

from auth import admin_despacho_requerido
from werkzeug.security import generate_password_hash

@empresas_bp.route("/mi-despacho", methods=["GET", "POST"])
@login_required
@admin_despacho_requerido
def mi_despacho():
    with get_connection() as con:
        usr_actual = con.execute(
            "SELECT organizacion_id, rol_global FROM usuarios WHERE id = ?",
            (session["usuario_id"],),
        ).fetchone()
        org_id = usr_actual["organizacion_id"]
        rol_asignador = usr_actual["rol_global"]

        if request.method == "POST":
            nombre = request.form["nombre"].strip()
            usuario = request.form["usuario"].strip().lower()
            password = request.form["password"]
            rol_raw = request.form.get("rol", "usuario")

            es_valido, resultado_rol = validar_rol_despacho_asignable(rol_raw, rol_asignador)
            if not es_valido:
                flash(resultado_rol, "error")
                return redirect(url_for("empresas.mi_despacho")), 400
            rol = resultado_rol

            existe = con.execute("SELECT id FROM usuarios WHERE usuario = ?", (usuario,)).fetchone()
            if existe:
                flash(f"El usuario {usuario} ya existe.", "error")
            else:
                con.execute(
                    "INSERT INTO usuarios (organizacion_id, nombre, usuario, password_hash, rol_global) VALUES (?, ?, ?, ?, ?)",
                    (org_id, nombre, usuario, generate_password_hash(password), rol),
                )
                con.commit()
                flash("Trabajador creado exitosamente.", "exito")
                return redirect(url_for("empresas.mi_despacho"))

        trabajadores = con.execute("""
            SELECT id, nombre, usuario, rol_global, activo, creado_en
            FROM usuarios
            WHERE organizacion_id = ?
            ORDER BY id DESC
        """, (org_id,)).fetchall()

    return render_template("mi_despacho.html", trabajadores=trabajadores)

@empresas_bp.route("/empresas/<int:empresa_id>/eliminar", methods=["POST"])
@login_required
@gerente_requerido
def eliminar_empresa(empresa_id):
    with get_connection() as con:
        # Verificar permisos (el usuario debe tener acceso a esta empresa o ser de la organizacion)
        # Por seguridad, revisamos si la empresa pertenece a la organizacion del usuario
        empresa = con.execute("SELECT organizacion_id FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
        if not empresa or empresa["organizacion_id"] != g.usuario["organizacion_id"]:
            flash("No tienes permiso para eliminar esta empresa.", "error")
            return redirect(url_for("empresas.selector_empresas"))
            
        # Eliminación en cascada manual simulada de la empresa (o depender del ON DELETE CASCADE)
        # Eliminamos movimientos y cfdis (aunque normalmente requieren cascada a cfdi_movimiento, etc.)
        # Lo más seguro es usar SQLite/Postgres CASCADE. Vamos a borrar los registros directos y que la DB haga el resto
        con.execute("DELETE FROM empresas WHERE id = ?", (empresa_id,))
        con.commit()
    flash("Empresa eliminada exitosamente.", "exito")
    return redirect(url_for("empresas.selector_empresas"))

def _float_seguro(valor, default=0.0) -> float:
    if valor is None:
        return default
    try:
        limpio = str(valor).strip().rstrip("%")
        return float(limpio)
    except (ValueError, TypeError):
        return default

@empresas_bp.route("/empresas/<int:empresa_id>/ajustes", methods=["GET", "POST"])
@login_required
@gerente_requerido
def ajustes_empresa(empresa_id):
    with get_connection() as con:
        empresa = con.execute("SELECT * FROM empresas WHERE id = ? AND organizacion_id = ?", (empresa_id, g.usuario["organizacion_id"])).fetchone()
        if not empresa:
            flash("Empresa no encontrada.", "error")
            return redirect(url_for("empresas.selector_empresas"))
            
        if request.method == "POST":
            tasa_iva = _float_seguro(request.form.get("tasa_iva"), 0.16)
            tasa_ret_iva = _float_seguro(request.form.get("tasa_retencion_iva"), 0.0)
            tasa_ret_isr = _float_seguro(request.form.get("tasa_retencion_isr"), 0.0)
            sql_servidor = request.form.get("sql_servidor", "").strip()
            sql_usuario = request.form.get("sql_usuario", "").strip()
            sql_password_input = request.form.get("sql_password", "").strip()
            sql_base_datos = request.form.get("sql_base_datos", "").strip()
            
            if sql_password_input:
                sql_password_final = cifrar_texto(sql_password_input)
            else:
                sql_password_final = empresa["sql_password"] or ""
            
            con.execute(
                "UPDATE empresas SET tasa_iva = ?, tasa_retencion_iva = ?, tasa_retencion_isr = ?, sql_servidor = ?, sql_usuario = ?, sql_password = ?, sql_base_datos = ? WHERE id = ?",
                (tasa_iva, tasa_ret_iva, tasa_ret_isr, sql_servidor, sql_usuario, sql_password_final, sql_base_datos, empresa_id)
            )
            con.commit()
            from auth import log_audit
            log_audit(g.usuario["organizacion_id"], g.usuario["id"], "Ajustes Empresa", f"Configuración contable actualizada para '{empresa['nombre']}'", con=con)
            flash("Configuración guardada exitosamente.", "exito")
            return redirect(url_for("empresas.ajustes_empresa", empresa_id=empresa_id))
            
    return render_template("empresa_ajustes.html", empresa=empresa)
