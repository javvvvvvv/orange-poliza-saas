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
from datetime import datetime
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../backend/app')))
from exporters.contpaqi_sdk_exporter import exportar_polizas_via_sdk

class LineaPoliza:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

class MovimientoContpaqi:
    def __init__(self, **kwargs):
        for k, v in kwargs.items():
            setattr(self, k, v)

from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for, send_file, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash

from auth import login_required, empresa_requerida, csrf_protect, obtener_empresas_del_usuario
from db import get_connection, hay_usuarios
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

polizas_bp = Blueprint('polizas', __name__)

# --- Pólizas ---

@polizas_bp.route("/empresas/<int:empresa_id>/polizas/generar", methods=["POST"])
@login_required
@empresa_requerida
def polizas_generar(empresa_id):
    inicio_i = request.form.get('inicio_ingreso', type=int)
    inicio_e = request.form.get('inicio_egreso', type=int)
    generadas = movimientos_repo.generar_polizas_pendientes(empresa_id, inicio_ingreso=inicio_i, inicio_egreso=inicio_e)
    flash(f"{int(generadas)} pólizas generadas y cuadradas.", "exito")
    descuadres = getattr(generadas, "descuadres", [])
    if descuadres:
        ejemplos = " | ".join(d["mensaje"] for d in descuadres[:3])
        extra = f" (+{len(descuadres) - 3} más)" if len(descuadres) > 3 else ""
        flash(
            f"Atención: {len(descuadres)} movimiento(s) no pudieron generar póliza por descuadre: {ejemplos}{extra}",
            "error",
        )
    return redirect(url_for("empresas.dashboard", empresa_id=empresa_id))

@polizas_bp.route("/empresas/<int:empresa_id>/polizas/exportar")
@login_required
@empresa_requerida
def polizas_exportar(empresa_id):
    con = get_connection()
    empresa = con.execute("SELECT * FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
    
    descuadradas = con.execute("SELECT COUNT(*) FROM polizas WHERE empresa_id = ? AND cuadrada = 0", (empresa_id,)).fetchone()[0]
    if descuadradas > 0:
        con.close()
        flash(f"CUIDADO: Tienes {descuadradas} póliza(s) descuadrada(s). Corrígelas antes de exportar.", "error")
        return redirect(url_for("empresas.dashboard", empresa_id=empresa_id))
    con.close()

    nombre_archivo = f"polizas_{empresa['nombre'].replace(' ', '_')}"
    ruta_salida = os.path.join(tempfile.gettempdir(), nombre_archivo)

    try:
        resultado = movimientos_repo.exportar_polizas(empresa_id, ruta_salida)
    except (movimientos_repo.SinPolizasPorExportarError, movimientos_repo.BancoSinCuentaContableError) as e:
        flash(str(e), "error")
        return redirect(url_for("empresas.dashboard", empresa_id=empresa_id))

    if "xlsx" in resultado["formato"]:
        flash(f"Se exportó en .xlsx en vez de .xls: {resultado['formato']}", "error")

    return send_file(resultado["archivo"], as_attachment=True,
                      download_name=os.path.basename(resultado["archivo"]))

def _construir_movimientos_contpaqi(con, polizas):
    movimientos_poliza = []
    map_lineas = {}
    map_uuids = {}
    if not polizas:
        return movimientos_poliza

    p_ids = [str(int(p["id"])) for p in polizas]
    lineas_raw = con.execute(
        f"SELECT * FROM poliza_lineas WHERE poliza_id IN ({','.join(p_ids)}) ORDER BY poliza_id, orden"
    ).fetchall()
    for l in lineas_raw:
        map_lineas.setdefault(l["poliza_id"], []).append(l)

    mov_ids = [str(int(p["mov_id"])) for p in polizas if p.get("mov_id") is not None]
    if mov_ids:
        cfdi_raw = con.execute(f"""
            SELECT cm.movimiento_id, c.uuid, c.uuids_relacionados
            FROM cfdi_movimiento cm
            JOIN cfdis c ON c.id = cm.cfdi_id
            WHERE cm.movimiento_id IN ({','.join(mov_ids)}) AND cm.confirmado = 1
        """).fetchall()
        for cf in cfdi_raw:
            uuid_final = cf["uuid"]
            if cf.get("uuids_relacionados"):
                uuids_lista = [u.strip() for u in cf["uuids_relacionados"].split(",") if u.strip()]
                if uuids_lista:
                    uuid_final = uuids_lista[0]
            if uuid_final:
                lista_u = map_uuids.setdefault(cf["movimiento_id"], [])
                if uuid_final not in lista_u:
                    lista_u.append(uuid_final)

    for p in polizas:
        lineas = []
        for l in map_lineas.get(p["id"], []):
            lineas.append(LineaPoliza(
                cuenta=l["cuenta"],
                naturaleza=l["naturaleza"],
                importe=l["importe"],
                descripcion=l["descripcion"],
                segmento_negocio=""
            ))
        uuids_poliza = map_uuids.get(p["mov_id"], []) if p.get("mov_id") else []
        mov = MovimientoContpaqi(
            fecha=datetime.strptime(p["fecha"], "%Y-%m-%d"),
            tipo=p["tipo"],
            numero_poliza=p["numero"],
            descripcion=p["concepto"] or f"Póliza {p['numero']}",
            lineas=lineas,
            numero_factura=p["referencia"] or "",
            cfdi_uuid=uuids_poliza[0] if len(uuids_poliza) == 1 else None,
            facturas=[LineaPoliza(uuid=u) for u in uuids_poliza]
        )
        movimientos_poliza.append(mov)
    return movimientos_poliza

@polizas_bp.route("/empresas/<int:empresa_id>/polizas/sdk_resumen", methods=["GET"])
@login_required
@empresa_requerida
def polizas_sdk_resumen(empresa_id):
    con = get_connection()
    empresa = con.execute(
        "SELECT nombre, base_datos_contpaqi FROM empresas WHERE id = ?", (empresa_id,)
    ).fetchone()
    if not empresa:
        con.close()
        return {"exito": False, "mensaje": "Empresa no encontrada."}, 404

    nombre_contpaqi = empresa["base_datos_contpaqi"]
    if not nombre_contpaqi:
        con.close()
        return {
            "exito": False,
            "mensaje": "Falta configurar la base de datos CONTPAQi para esta empresa (IVA y bancos / Catálogo).",
        }, 400

    descuadradas = con.execute(
        "SELECT COUNT(*) FROM polizas WHERE empresa_id = ? AND cuadrada = 0", (empresa_id,)
    ).fetchone()[0]
    if descuadradas > 0:
        con.close()
        return {
            "exito": False,
            "mensaje": f"No se puede exportar: tienes {descuadradas} póliza(s) descuadrada(s). Corrígelas antes de enviar a CONTPAQi.",
        }, 400

    polizas = con.execute("""
        SELECT p.id, m.id AS mov_id
        FROM polizas p
        LEFT JOIN movimientos m ON m.poliza_id = p.id
        WHERE p.empresa_id = ?
        ORDER BY p.tipo, p.numero
    """, (empresa_id,)).fetchall()

    poliza_ids = [p["id"] for p in polizas]
    mov_ids = [str(int(p["mov_id"])) for p in polizas if p["mov_id"] is not None]
    con_cfdi = 0
    if mov_ids:
        con_cfdi = con.execute(f"""
            SELECT COUNT(DISTINCT cm.movimiento_id)
            FROM cfdi_movimiento cm
            WHERE cm.movimiento_id IN ({','.join(mov_ids)}) AND cm.confirmado = 1
        """).fetchone()[0]
    con.close()

    return {
        "exito": True,
        "nombre_contpaqi": nombre_contpaqi,
        "poliza_ids": poliza_ids,
        "total_polizas": len(poliza_ids),
        "con_cfdi": con_cfdi,
    }

@polizas_bp.route("/empresas/<int:empresa_id>/polizas/exportar_sdk_lote", methods=["POST"])
@login_required
@empresa_requerida
@csrf_protect
def exportar_polizas_sdk_lote(empresa_id):
    datos = request.get_json(silent=True) or {}
    lote_ids = [int(x) for x in (datos.get("poliza_ids") or []) if str(x).isdigit()]
    if not lote_ids:
        return {"exito": False, "mensaje": "Lote vacío de pólizas."}, 400

    con = get_connection()
    empresa = con.execute(
        "SELECT nombre, base_datos_contpaqi FROM empresas WHERE id = ?", (empresa_id,)
    ).fetchone()
    if not empresa or not empresa["base_datos_contpaqi"]:
        con.close()
        return {"exito": False, "mensaje": "Falta configurar base_datos_contpaqi."}, 400

    nombre_contpaqi = empresa["base_datos_contpaqi"]
    ids_str = ",".join(str(i) for i in lote_ids)
    polizas = con.execute(f"""
        SELECT p.*, m.id AS mov_id, COALESCE(p.concepto, m.descripcion) AS concepto
        FROM polizas p
        LEFT JOIN movimientos m ON m.poliza_id = p.id
        WHERE p.empresa_id = ? AND p.id IN ({ids_str})
        ORDER BY p.tipo, p.numero
    """, (empresa_id,)).fetchall()

    movimientos_poliza = _construir_movimientos_contpaqi(con, polizas)
    con.close()

    try:
        res = exportar_polizas_via_sdk(movimientos_poliza, nombre_interno_empresa=nombre_contpaqi)
        if res.get("exito"):
            con_up = get_connection()
            ahora_str = datetime.now().isoformat()
            con_up.execute(
                f"UPDATE polizas SET enviada_contpaqi = 1, fecha_envio = ? WHERE id IN ({ids_str})",
                (ahora_str,),
            )
            con_up.commit()
            con_up.close()
            return {
                "exito": True,
                "enviadas": len(movimientos_poliza),
                "mensaje": res.get("mensaje", "Lote enviado correctamente."),
            }
        return {
            "exito": False,
            "mensaje": res.get("mensaje", "Error desconocido al enviar lote al SDK."),
        }, 500
    except Exception as e:
        return {"exito": False, "mensaje": f"Error interno: {e}"}, 500

@polizas_bp.route("/empresas/<int:empresa_id>/polizas/reconciliar_cfdi", methods=["POST"])
@login_required
@empresa_requerida
@csrf_protect
def reconciliar_cfdi_polizas(empresa_id):
    res = cfdi_repo.conciliar_existentes(empresa_id)
    nuevos = len(res.get("conciliados", []))
    flash(f"Conciliación CFDI ejecutada: {nuevos} movimiento(s) asociado(s) con sus facturas.", "exito")
    return redirect(url_for("polizas.polizas_revision", empresa_id=empresa_id))

@polizas_bp.route("/empresas/<int:empresa_id>/polizas/exportar_sdk", methods=["POST"])
@login_required
@empresa_requerida
@csrf_protect
def exportar_polizas_sdk(empresa_id):
    con = get_connection()
    empresa = con.execute(
        "SELECT nombre, base_datos_contpaqi FROM empresas WHERE id = ?", (empresa_id,)
    ).fetchone()
    if not empresa:
        flash("Empresa no encontrada.", "error")
        con.close()
        return redirect(url_for("empresas.dashboard", empresa_id=empresa_id))

    nombre_contpaqi = empresa["base_datos_contpaqi"]
    if not nombre_contpaqi:
        flash(
            "Falta configurar el nombre interno de CONTPAQi para esta empresa "
            "(Configuración > Catálogo, base de datos CONTPAQi).",
            "error",
        )
        con.close()
        return redirect(url_for("empresas.dashboard", empresa_id=empresa_id))

    # VERIFICACION DE ROBUSTEZ: No exportar polizas descuadradas
    descuadradas = con.execute("SELECT COUNT(*) FROM polizas WHERE empresa_id = ? AND cuadrada = 0", (empresa_id,)).fetchone()[0]
    if descuadradas > 0:
        flash(f"CUIDADO: No se puede exportar. Tienes {descuadradas} póliza(s) descuadrada(s). Revisa el visor y corrígelas antes de enviar a CONTPAQi.", "error")
        con.close()
        return redirect(url_for("empresas.dashboard", empresa_id=empresa_id))

    reexportar = request.args.get("reexportar", "0") == "1"
    filtro_envio = "" if reexportar else " AND p.enviada_contpaqi = 0"
    polizas = con.execute(f"""
        SELECT p.*, m.id AS mov_id, COALESCE(p.concepto, m.descripcion) AS concepto 
        FROM polizas p 
        LEFT JOIN movimientos m ON m.poliza_id = p.id 
        WHERE p.empresa_id = ? AND p.cuadrada = 1{filtro_envio} ORDER BY p.tipo, p.numero
    """, (empresa_id,)).fetchall()

    if not polizas:
        flash("No hay pólizas cuadradas pendientes de exportar.", "info")
        con.close()
        return redirect(url_for("empresas.dashboard", empresa_id=empresa_id))

    movimientos_poliza = _construir_movimientos_contpaqi(con, polizas)
    con.close()

    try:
        res = exportar_polizas_via_sdk(movimientos_poliza, nombre_interno_empresa=nombre_contpaqi)
        
        if res.get("exito"):
            con_up = get_connection()
            ahora_str = datetime.now().isoformat()
            p_ids_all = [str(int(p["id"])) for p in polizas]
            if p_ids_all:
                con_up.execute(
                    f"UPDATE polizas SET enviada_contpaqi = 1, fecha_envio = ? WHERE id IN ({','.join(p_ids_all)})",
                    (ahora_str,),
                )
                con_up.commit()
            con_up.close()
            flash(f"Éxito: {res.get('mensaje')}", "exito")
            return redirect(url_for("empresas.dashboard", empresa_id=empresa_id))
        else:
            flash(f"Error SDK: {res.get('mensaje')}", "error")
            return redirect(url_for("empresas.dashboard", empresa_id=empresa_id))
    except Exception as e:
        flash(f"Error interno: {e}", "error")
        return redirect(url_for("empresas.dashboard", empresa_id=empresa_id))

# --- Revisión de pólizas generadas ---

@polizas_bp.route("/empresas/<int:empresa_id>/polizas")
@login_required
@empresa_requerida
def polizas_revision(empresa_id):
    con = get_connection()
    polizas = con.execute(
        """SELECT p.*, m.id AS mov_id, m.descripcion AS descripcion_movimiento, m.numero_factura
           FROM polizas p LEFT JOIN movimientos m ON m.poliza_id = p.id
           WHERE p.empresa_id = ? ORDER BY p.fecha DESC, p.tipo, p.numero""",
        (empresa_id,),
    ).fetchall()

    map_lineas = {}
    map_explicacion = {}
    map_cfdis = {}
    if polizas:
        p_ids = [str(int(p["id"])) for p in polizas]
        for l in con.execute(f"SELECT * FROM poliza_lineas WHERE poliza_id IN ({','.join(p_ids)}) ORDER BY poliza_id, orden").fetchall():
            map_lineas.setdefault(l["poliza_id"], []).append(l)
        for e in con.execute(f"SELECT poliza_id, detalle FROM poliza_auditoria WHERE poliza_id IN ({','.join(p_ids)}) ORDER BY poliza_id, paso").fetchall():
            map_explicacion.setdefault(e["poliza_id"], []).append(e["detalle"])

        mov_ids = [str(int(p["mov_id"])) for p in polizas if p["mov_id"] is not None]
        if mov_ids:
            cfdi_rows = con.execute(f"""
                SELECT cm.movimiento_id, cm.importe_aplicado, cm.tipo_match,
                       c.uuid, c.serie, c.folio, c.total, c.rfc_emisor, c.rfc_receptor, c.nombre_emisor, c.nombre_receptor
                FROM cfdi_movimiento cm
                JOIN cfdis c ON c.id = cm.cfdi_id
                WHERE cm.movimiento_id IN ({','.join(mov_ids)}) AND cm.confirmado = 1
            """).fetchall()
            for cf in cfdi_rows:
                map_cfdis.setdefault(cf["movimiento_id"], []).append(cf)

    detalle = {}
    total_con_cfdi = 0
    for p in polizas:
        cfdis_p = map_cfdis.get(p["mov_id"], []) if p["mov_id"] else []
        if cfdis_p:
            total_con_cfdi += 1
        detalle[p["id"]] = {
            "lineas": map_lineas.get(p["id"], []),
            "explicacion": map_explicacion.get(p["id"], []),
            "cfdis": cfdis_p,
        }
    total_cfdis_empresa = con.execute("SELECT COUNT(*) FROM cfdis WHERE empresa_id = ?", (empresa_id,)).fetchone()[0]
    con.close()

    return render_template(
        "polizas.html",
        polizas=polizas,
        detalle=detalle,
        total_con_cfdi=total_con_cfdi,
        total_cfdis_empresa=total_cfdis_empresa,
    )

@polizas_bp.route("/empresas/<int:empresa_id>/polizas/limpiar", methods=["POST"])
@login_required
@empresa_requerida
@csrf_protect
def polizas_limpiar(empresa_id):
    movimientos_repo.limpiar_polizas(empresa_id)
    flash("Se han eliminado todas las pólizas generadas. Los movimientos vuelven a estar pendientes.", "exito")
    return redirect(url_for("empresas.dashboard", empresa_id=empresa_id))

@polizas_bp.route("/empresas/<int:empresa_id>/movimientos/limpiar", methods=["POST"])
@login_required
@empresa_requerida
@csrf_protect
def movimientos_limpiar(empresa_id):
    forzar = request.form.get("forzar") == "1"
    try:
        movimientos_repo.limpiar_movimientos(empresa_id, forzar=forzar)
        flash("Se han eliminado los movimientos y pólizas pendientes. Lista para una carga nueva.", "exito")
    except movimientos_repo.PolizasYaEnviadasError as e:
        flash(str(e), "error")
    return redirect(url_for("empresas.dashboard", empresa_id=empresa_id))

@polizas_bp.route("/empresas/<int:empresa_id>/polizas/<int:poliza_id>/editar", methods=["POST"])
@login_required
@empresa_requerida
@csrf_protect
def polizas_editar(empresa_id, poliza_id):
    datos = request.get_json(silent=True)
    if not datos or "lineas" not in datos:
        return {"exito": False, "mensaje": "Datos invalidos"}, 400
        
    try:
        res = movimientos_repo.actualizar_poliza(poliza_id, datos["lineas"], empresa_id=empresa_id)
        return {
            "exito": True,
            "mensaje": "Poliza actualizada correctamente.",
            "cuadrada": res["cuadrada"],
            "cargos": float(res["cargos"]),
            "abonos": float(res["abonos"]),
            "diferencia": float(res["diferencia"]),
        }
    except ValueError as e:
        return {"exito": False, "mensaje": str(e)}, 404
    except Exception as e:
        return {"exito": False, "mensaje": str(e)}, 500
