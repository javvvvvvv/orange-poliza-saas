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
from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for, send_file, send_from_directory, jsonify
from werkzeug.security import check_password_hash, generate_password_hash

from auth import (
    login_required,
    empresa_requerida,
    obtener_empresas_del_usuario,
    validar_rol_empresa_asignable,
)
from db import get_connection, hay_usuarios
from upload_security import (
    ruta_segura_en_directorio,
    ErrorArchivoInseguro,
    EXTENSIONES_CATALOGO,
)
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
from polizas_edicion import obtener_asiento_movimiento, guardar_asiento_movimiento

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOADS_DIR = os.path.join(BASE_DIR, '..', "uploads")

def _mover_archivo_con_reintentos(origen, destino, intentos=5, espera_segundos=0.4):
    import time
    ultimo_error = None
    for intento in range(intentos):
        try:
            if os.path.exists(destino):
                try:
                    os.chmod(destino, 0o777)
                except OSError:
                    pass
                try:
                    os.remove(destino)
                except OSError:
                    pass
            shutil.move(origen, destino)
            try:
                os.chmod(destino, 0o666)
            except OSError:
                pass
            return
        except (PermissionError, OSError) as e:
            ultimo_error = e
            time.sleep(espera_segundos)
    try:
        if os.path.exists(destino):
            try:
                os.chmod(destino, 0o777)
            except OSError:
                pass
            try:
                os.remove(destino)
            except OSError:
                pass
        shutil.copy2(origen, destino)
        try:
            os.chmod(destino, 0o666)
        except OSError:
            pass
    except Exception:
        raise ultimo_error
    try:
        os.remove(origen)
    except OSError:
        pass

configuracion_bp = Blueprint('configuracion', __name__)

# --- Catálogo de cuentas ---

def _ruta_estado_sync(empresa_id: int) -> str:
    return os.path.join(tempfile.gettempdir(), f"orange_sync_catalogo_{int(empresa_id)}.json")

def _leer_estado_sync(empresa_id: int) -> dict:
    import json
    ruta = _ruta_estado_sync(empresa_id)
    if not os.path.exists(ruta):
        return {}
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except Exception:
        return {}

def _guardar_estado_sync(empresa_id: int, estado: dict) -> None:
    import json
    ruta = _ruta_estado_sync(empresa_id)
    ruta_tmp = f"{ruta}.{uuid.uuid4().hex}.tmp"
    try:
        with open(ruta_tmp, "w", encoding="utf-8") as f:
            json.dump(estado, f, ensure_ascii=False)
        os.replace(ruta_tmp, ruta)
    except Exception:
        try:
            if os.path.exists(ruta_tmp):
                os.remove(ruta_tmp)
        except OSError:
            pass

@configuracion_bp.route("/empresas/<int:empresa_id>/catalogo", methods=["GET", "POST"])
@login_required
@empresa_requerida
def catalogo(empresa_id):
    if request.method == "POST":
        modo = request.form.get("modo")
        try:
            if modo == "archivo":
                archivo = request.files.get("archivo_catalogo")
                if not archivo or not archivo.filename:
                    flash("Selecciona un archivo de Excel.", "error")
                    return redirect(url_for("configuracion.catalogo", empresa_id=empresa_id))
                ruta_temporal = ruta_segura_en_directorio(
                    tempfile.gettempdir(),
                    archivo.filename,
                    extensiones_permitidas=EXTENSIONES_CATALOGO,
                    prefijo_unico=True,
                )
                try:
                    archivo.save(ruta_temporal)
                    total = catalogo_repo.importar_desde_excel(empresa_id, ruta_temporal)
                finally:
                    try:
                        if os.path.exists(ruta_temporal):
                            os.remove(ruta_temporal)
                    except OSError:
                        pass
            elif modo == "texto":
                total = catalogo_repo.importar_desde_texto(empresa_id, request.form.get("texto_catalogo", ""))
            elif modo == "contpaqi":
                with get_connection() as con:
                    ruta_contpaqi = con.execute("SELECT base_datos_contpaqi FROM empresas WHERE id = ?", (empresa_id,)).fetchone()[0]
                if not ruta_contpaqi:
                    raise Exception("Falta configurar la ruta CONTPAQi de la empresa en Configuracion.")
                forzar = request.form.get("forzar") == "1"
                total = catalogo_repo.sincronizar_desde_contpaqi(empresa_id, ruta_contpaqi, forzar=forzar)
            else:
                raise Exception("Modo no valido")
            flash(f"{total} cuentas importadas/actualizadas en el catálogo.", "exito")
        except Exception as e:
            flash(f"Error al importar catálogo: {e}", "error")
        return redirect(url_for("configuracion.catalogo", empresa_id=empresa_id))

    buscar = request.args.get("q")
    cuentas = catalogo_repo.listar(empresa_id, buscar=buscar)
    with get_connection() as con:
        fila_emp = con.execute("SELECT base_datos_contpaqi FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
        base_datos_contpaqi = fila_emp[0] if fila_emp else None
    return render_template(
        "catalogo.html",
        cuentas=cuentas,
        buscar=buscar or "",
        base_datos_contpaqi=base_datos_contpaqi or "",
    )

@configuracion_bp.route("/empresas/<int:empresa_id>/catalogo/sincronizar-async", methods=["POST"])
@login_required
@empresa_requerida
def catalogo_sincronizar_async(empresa_id):
    import threading
    import time

    with get_connection() as con:
        fila = con.execute(
            "SELECT base_datos_contpaqi, (SELECT COUNT(*) FROM cuentas_catalogo WHERE empresa_id = ?) FROM empresas WHERE id = ?",
            (empresa_id, empresa_id),
        ).fetchone()
    ruta_contpaqi = fila[0] if fila else None
    total_previo = int(fila[1] or 0) if fila else 0

    if not ruta_contpaqi:
        return {
            "exito": False,
            "error": "Falta configurar la base de datos CONTPAQi de la empresa en Ajustes (IVA y bancos).",
        }, 400

    payload = request.get_json(silent=True) or {}
    forzar = bool(payload.get("forzar", False))

    estado_actual = _leer_estado_sync(empresa_id)
    ahora = time.time()
    if estado_actual.get("en_curso") and (ahora - float(estado_actual.get("inicio_ts", 0))) < 900:
        return {
            "exito": True,
            "en_curso": True,
            "empresa_contpaqi": ruta_contpaqi,
            "mensaje": "Sincronización en curso.",
        }

    estado_inicial = {
        "en_curso": True,
        "terminado": False,
        "fase": "conectando",
        "etapa": f"Conectando con CONTPAQi ({ruta_contpaqi})...",
        "leidas": 0,
        "total_estimado": total_previo,
        "porcentaje": 2.0,
        "segundos": 0.0,
        "velocidad": 0.0,
        "ultima_cuenta": None,
        "importadas": 0,
        "desde_cache": False,
        "error": None,
        "inicio_ts": ahora,
        "empresa_contpaqi": ruta_contpaqi,
    }
    _guardar_estado_sync(empresa_id, estado_inicial)

    def _trabajador():
        t0 = time.time()

        def _on_etapa(fase: str, leidas_actual: int, estimado_actual: int):
            est = _leer_estado_sync(empresa_id) or dict(estado_inicial)
            est["fase"] = fase
            est["segundos"] = round(time.time() - t0, 1)
            if fase == "leyendo_sdk":
                est["etapa"] = f"Extrayendo catálogo de {ruta_contpaqi} desde CONTPAQi..."
                est["porcentaje"] = max(float(est.get("porcentaje") or 4.0), 5.0)
            elif fase == "guardando_bd":
                est["leidas"] = int(leidas_actual)
                est["total_estimado"] = int(estimado_actual)
                est["porcentaje"] = 96.0
                est["etapa"] = f"Guardando e indexando jerarquía de {leidas_actual:,} cuentas en base de datos..."
            _guardar_estado_sync(empresa_id, est)

        try:
            total = catalogo_repo.sincronizar_desde_contpaqi(
                empresa_id,
                ruta_contpaqi,
                forzar=forzar,
                callback_etapa=_on_etapa,
            )
            nuevas = int(getattr(total, "nuevas", 0))
            reemplazadas = int(getattr(total, "reemplazadas", 0))
            iguales = int(getattr(total, "iguales", 0))
            modo_sync = str(getattr(total, "modo", "incremental"))
            duracion = round(max(0.1, time.time() - t0), 1)
            est_fin = _leer_estado_sync(empresa_id) or dict(estado_inicial)
            if nuevas > 0 or reemplazadas > 0:
                msg_etapa = (
                    f"¡Catálogo actualizado! +{nuevas:,} nueva(s), "
                    f"{reemplazadas:,} reemplazada(s), {iguales:,} sin cambios ({int(total):,} en total)."
                )
            else:
                msg_etapa = (
                    f"¡Catálogo al día! 0 diferencias detectadas por ID+Cuenta+Nombre "
                    f"({int(total):,} cuentas verificadas)."
                )
            est_fin.update({
                "en_curso": False,
                "terminado": True,
                "fase": "completado",
                "etapa": msg_etapa,
                "leidas": int(total),
                "total_estimado": int(total),
                "importadas": int(total),
                "nuevas": nuevas,
                "reemplazadas": reemplazadas,
                "iguales": iguales,
                "modo": modo_sync,
                "porcentaje": 100.0,
                "segundos": duracion,
                "velocidad": round(int(total) / duracion, 1) if duracion > 0 else int(total),
                "error": None,
            })
            _guardar_estado_sync(empresa_id, est_fin)
        except Exception as e:
            duracion = round(max(0.1, time.time() - t0), 1)
            est_err = _leer_estado_sync(empresa_id) or dict(estado_inicial)
            est_err.update({
                "en_curso": False,
                "terminado": True,
                "fase": "error",
                "etapa": "Error durante la sincronización",
                "segundos": duracion,
                "error": str(e),
            })
            _guardar_estado_sync(empresa_id, est_err)

    hilo = threading.Thread(target=_trabajador, daemon=True)
    hilo.start()
    return {
        "exito": True,
        "en_curso": True,
        "empresa_contpaqi": ruta_contpaqi,
    }

@configuracion_bp.route("/empresas/<int:empresa_id>/catalogo/progreso", methods=["GET"])
@login_required
@empresa_requerida
def catalogo_progreso(empresa_id):
    import time

    estado = _leer_estado_sync(empresa_id)
    if not estado:
        return {
            "exito": True,
            "en_curso": False,
            "terminado": False,
            "porcentaje": 0.0,
            "leidas": 0,
            "total_estimado": 0,
            "etapa": "Sin sincronización activa",
        }

    ahora = time.time()
    inicio_ts = float(estado.get("inicio_ts") or ahora)
    if estado.get("en_curso"):
        estado["segundos"] = round(max(0.1, ahora - inicio_ts), 1)

    if estado.get("en_curso") and estado.get("fase") in ("conectando", "leyendo_sdk"):
        emp_contpaqi = estado.get("empresa_contpaqi")
        if emp_contpaqi:
            try:
                from contpaqi_bridge_client import obtener_progreso_cuentas
                res_bridge = obtener_progreso_cuentas(emp_contpaqi)
                prog = (res_bridge or {}).get("progreso") or {}
                if prog:
                    leidas_b = int(prog.get("leidas") or 0)
                    est_b = int(prog.get("totalEstimado") or estado.get("total_estimado") or 0)
                    pct_b = float(prog.get("porcentaje") or 0.0)
                    vel_b = float(prog.get("cuentasPorSegundo") or 0.0)
                    etapa_b = prog.get("etapa")
                    ult_b = prog.get("ultimaCuenta")
                    desde_cache_b = bool(prog.get("desdeCache", False))

                    if leidas_b > 0 or pct_b > 0:
                        estado["leidas"] = leidas_b
                        if est_b > 0:
                            estado["total_estimado"] = est_b
                        # En fase leyendo_sdk limitamos a 95% para reservar 96-100% al guardado en PostgreSQL
                        estado["porcentaje"] = min(95.0, max(float(estado.get("porcentaje") or 2.0), pct_b))
                        estado["velocidad"] = vel_b
                        if etapa_b and not prog.get("terminado"):
                            estado["etapa"] = etapa_b
                        if ult_b:
                            estado["ultima_cuenta"] = ult_b
                        estado["desde_cache"] = desde_cache_b
            except Exception:
                pass

    return {"exito": True, **estado}

@configuracion_bp.route("/empresas/<int:empresa_id>/catalogo/eliminar-todo", methods=["POST"])
@login_required
@empresa_requerida
def catalogo_eliminar_todo(empresa_id):
    catalogo_repo.eliminar_todo(empresa_id)
    flash("Catálogo eliminado.", "exito")
    return redirect(url_for("configuracion.catalogo", empresa_id=empresa_id))

# --- Configuración: IVA y bancos ---

@configuracion_bp.route("/empresas/<int:empresa_id>/configuracion", methods=["GET", "POST"])
@login_required
@empresa_requerida
def configuracion(empresa_id):
    empresas_bridge = []
    try:
        import sys, os
        sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../../backend/app/exporters')))
        from contpaqi_bridge_client import listar_empresas
        res = listar_empresas()
        if res.get('exito'):
            empresas_bridge = res.get('empresas', [])
    except Exception as e:
        print(f"Error cargando empresas del bridge: {e}")

    if request.method == "POST":
        datos = {
            "tasa_iva": float(request.form.get("tasa_iva", 0.16)),
            "cuenta_iva_acreditable": request.form.get("cuenta_iva_acreditable") or None,
            "cuenta_iva_por_acreditar": request.form.get("cuenta_iva_por_acreditar") or None,
            "cuenta_iva_trasladado": request.form.get("cuenta_iva_trasladado") or None,
            "cuenta_iva_por_trasladar": request.form.get("cuenta_iva_por_trasladar") or None,
            "cuenta_complementaria_ingresos": request.form.get("cuenta_complementaria_ingresos") or None,
            "cuenta_complementaria_egresos": request.form.get("cuenta_complementaria_egresos") or None,
            "cuenta_dif_cambiaria": request.form.get("cuenta_dif_cambiaria") or None,
            "retenciones_activas": request.form.get("retenciones_activas") == "on",
            "cuenta_retencion_iva": request.form.get("cuenta_retencion_iva") or None,
            "cuenta_retencion_isr": request.form.get("cuenta_retencion_isr") or None,
            "cuenta_anticipo_clientes": request.form.get("cuenta_anticipo_clientes") or None,
            "cuenta_anticipo_proveedores": request.form.get("cuenta_anticipo_proveedores") or None,
            "base_datos_contpaqi": request.form.get("base_datos_contpaqi") or None,
        }
        print(f"DATOS: {datos}", flush=True); bancos_repo.actualizar_configuracion_iva(empresa_id, datos)
        flash("Configuración guardada.", "exito")
        return redirect(url_for("configuracion.configuracion", empresa_id=empresa_id))

    empresa = bancos_repo.obtener_empresa(empresa_id)
    bancos = bancos_repo.listar_bancos(empresa_id)
    cuentas = catalogo_repo.listar(empresa_id)
    reglas_generales_ingreso = reglas_generales_repo.listar(empresa_id, "ingreso")
    reglas_generales_egreso = reglas_generales_repo.listar(empresa_id, "egreso")
    return render_template(
        "configuracion.html", empresa=empresa, bancos=bancos, cuentas=cuentas,
        reglas_generales_ingreso=reglas_generales_ingreso,
        reglas_generales_egreso=reglas_generales_egreso,
        empresas_bridge=empresas_bridge
    )

@configuracion_bp.route("/empresas/<int:empresa_id>/bancos/nuevo", methods=["POST"])
@login_required
@empresa_requerida
def banco_nuevo(empresa_id):
    bancos_repo.crear_banco(
        empresa_id, request.form["nombre"], request.form["cuenta_contable"],
        request.form.get("moneda", "MXN"),
    )
    flash("Banco agregado.", "exito")
    return redirect(url_for("configuracion.configuracion", empresa_id=empresa_id))

@configuracion_bp.route("/empresas/<int:empresa_id>/bancos/<int:banco_id>/eliminar", methods=["POST"])
@login_required
@empresa_requerida
def banco_eliminar(empresa_id, banco_id):
    eliminado = bancos_repo.eliminar_banco(banco_id, empresa_id=empresa_id)
    if not eliminado:
        flash("Banco no encontrado en esta empresa.", "error")
        return redirect(url_for("configuracion.configuracion", empresa_id=empresa_id)), 404
    flash("Banco eliminado.", "exito")
    return redirect(url_for("configuracion.configuracion", empresa_id=empresa_id))

@configuracion_bp.route("/empresas/<int:empresa_id>/reglas-generales/nueva", methods=["POST"])
@login_required
@empresa_requerida
def regla_general_nueva(empresa_id):
    reglas_generales_repo.agregar(
        empresa_id, request.form["tipo_movimiento"], request.form["cuenta"],
        request.form["naturaleza"], request.form["formula"],
        request.form.get("descripcion_linea") or None,
    )
    flash("Línea agregada a las reglas generales.", "exito")
    return redirect(url_for("configuracion.configuracion", empresa_id=empresa_id))

@configuracion_bp.route("/empresas/<int:empresa_id>/reglas-generales/<int:linea_id>/eliminar", methods=["POST"])
@login_required
@empresa_requerida
def regla_general_eliminar(empresa_id, linea_id):
    eliminada = reglas_generales_repo.eliminar(linea_id, empresa_id=empresa_id)
    if not eliminada:
        flash("Línea no encontrada en esta empresa.", "error")
        return redirect(url_for("configuracion.configuracion", empresa_id=empresa_id)), 404
    flash("Línea eliminada.", "exito")
    return redirect(url_for("configuracion.configuracion", empresa_id=empresa_id))

# --- Documentos importados y estado de cuenta marcado ---

@configuracion_bp.route("/empresas/<int:empresa_id>/documentos")
@login_required
@empresa_requerida
def documentos(empresa_id):
    with get_connection() as con:
        filas = con.execute(
            """SELECT d.*, b.nombre AS banco_nombre,
                      (SELECT COUNT(*) FROM movimientos m WHERE m.documento_id = d.id) AS total_movimientos,
                      (SELECT COUNT(*) FROM movimientos m WHERE m.documento_id = d.id AND m.poliza_id IS NOT NULL) AS con_poliza
               FROM documentos_importados d
               LEFT JOIN bancos b ON b.id = d.banco_id
               WHERE d.empresa_id = ? ORDER BY d.importado_en DESC""",
            (empresa_id,),
        ).fetchall()
    return render_template("documentos.html", documentos=filas)

@configuracion_bp.route("/empresas/<int:empresa_id>/documentos/<int:documento_id>/marcado")
@login_required
@empresa_requerida
def documento_marcado(empresa_id, documento_id):
    try:
        ruta, marcados = marcado_repo.generar_marcado(documento_id, empresa_id=empresa_id)
    except ValueError as e:
        flash(str(e), "error")
        return redirect(url_for("configuracion.documentos", empresa_id=empresa_id))

    if marcados == 0:
        flash("Ningún movimiento de este documento tiene póliza generada todavía.", "error")

    return send_file(ruta, as_attachment=True, download_name=os.path.basename(ruta))

@configuracion_bp.route("/empresas/<int:empresa_id>/documentos/<int:documento_id>/remap")
@login_required
@empresa_requerida
def documento_remap(empresa_id, documento_id):
    """
    Permite volver a abrir el asistente de lectura de columnas para un documento ya importado,
    corrigiendo errores de mapeo (ej. si se leyó columna equivocada de ingreso/egreso o encabezado).
    """
    with get_connection() as con:
        doc = con.execute(
            "SELECT * FROM documentos_importados WHERE id = %s AND empresa_id = %s",
            (documento_id, empresa_id)
        ).fetchone()

    if not doc:
        flash("El documento no existe.", "error")
        return redirect(url_for("configuracion.documentos", empresa_id=empresa_id))

    ruta_archivo = doc["ruta_archivo"]
    if not ruta_archivo or not os.path.exists(ruta_archivo):
        # Fallback a carpeta uploads/{empresa_id}/
        ruta_alt = os.path.join(UPLOADS_DIR, str(empresa_id), doc["nombre_archivo"])
        if os.path.exists(ruta_alt):
            ruta_archivo = ruta_alt
        else:
            flash(f"El archivo original '{doc['nombre_archivo']}' ya no se encuentra en el servidor. Puedes volver a subirlo.", "error")
            return redirect(url_for("configuracion.documentos", empresa_id=empresa_id))

    token = uuid.uuid4().hex
    carpeta_temporal = os.path.join(UPLOADS_DIR, "_tmp", token)
    os.makedirs(carpeta_temporal, exist_ok=True)
    ruta_copia = os.path.join(carpeta_temporal, doc["nombre_archivo"])
    shutil.copy2(ruta_archivo, ruta_copia)

    session[f"preview_{token}"] = {
        "archivos": [ruta_copia],
        "banco_id": doc["banco_id"],
        "documento_id_remap": documento_id,
    }

    return redirect(url_for("importacion.importar_vista_previa", empresa_id=empresa_id, token=token, documento_id_remap=documento_id))


@configuracion_bp.route("/empresas/<int:empresa_id>/documentos/<int:documento_id>/eliminar", methods=["POST"])
@login_required
@empresa_requerida
def documento_eliminar(empresa_id, documento_id):
    """
    Elimina un documento importado por error junto con sus movimientos y asociaciones.
    Libera el hash para permitir volver a subir el archivo desde cero si se desea.
    """
    with get_connection() as con:
        cur = con.cursor()
        doc = cur.execute(
            "SELECT * FROM documentos_importados WHERE id = %s AND empresa_id = %s",
            (documento_id, empresa_id)
        ).fetchone()

        if not doc:
            flash("El documento no existe.", "error")
            return redirect(url_for("configuracion.documentos", empresa_id=empresa_id))

        # 1. Eliminar asociaciones de CFDI de los movimientos de este documento
        cur.execute("DELETE FROM cfdi_movimiento WHERE movimiento_id IN (SELECT id FROM movimientos WHERE documento_id = %s)", (documento_id,))

        # 2. Desvincular y eliminar pólizas asociadas a los movimientos del documento
        polizas_antiguas = cur.execute("SELECT DISTINCT poliza_id FROM movimientos WHERE documento_id = %s AND poliza_id IS NOT NULL", (documento_id,)).fetchall()
        cur.execute("UPDATE movimientos SET poliza_id = NULL WHERE documento_id = %s", (documento_id,))
        for p_row in polizas_antiguas:
            pid = p_row[0] if isinstance(p_row, (tuple, list)) else p_row["poliza_id"]
            if pid:
                cur.execute("DELETE FROM poliza_lineas WHERE poliza_id = %s", (pid,))
                cur.execute("DELETE FROM poliza_auditoria WHERE poliza_id = %s", (pid,))
                cur.execute("DELETE FROM polizas WHERE id = %s", (pid,))

        # 3. Eliminar movimientos
        cur.execute("DELETE FROM movimientos WHERE documento_id = %s", (documento_id,))

        # 4. Eliminar registro del documento
        cur.execute("DELETE FROM documentos_importados WHERE id = %s", (documento_id,))
        con.commit()

    # 5. Eliminar archivo físico de uploads si existe
    try:
        if doc.get("ruta_archivo") and os.path.exists(doc["ruta_archivo"]):
            os.remove(doc["ruta_archivo"])
    except OSError:
        pass

    flash(f"Documento '{doc['nombre_archivo']}' y sus movimientos fueron eliminados correctamente.", "exito")
    return redirect(url_for("configuracion.documentos", empresa_id=empresa_id))


@configuracion_bp.route("/empresas/<int:empresa_id>/documentos/<int:documento_id>/movimientos-json")
@login_required
@empresa_requerida
def documento_movimientos_json(empresa_id, documento_id):
    """
    Retorna en JSON los movimientos leídos de un documento para inspección y edición inline.
    """
    with get_connection() as con:
        movs = con.execute(
            """SELECT m.id, m.fecha, m.descripcion, m.tipo, m.total, m.rfc_contraparte, m.referencia_bancaria,
                      m.numero_factura, m.estado_clasificacion, m.poliza_id,
                      (
                          SELECT string_agg(DISTINCT COALESCE(NULLIF(TRIM(COALESCE(c.serie, '') || COALESCE(c.folio, '')), ''), 'S/F'), ', ')
                          FROM cfdi_movimiento cm
                          JOIN cfdis c ON c.id = cm.cfdi_id
                          WHERE cm.movimiento_id = m.id AND cm.confirmado = 1
                      ) AS facturas_cfdi
               FROM movimientos m
               WHERE m.empresa_id = %s AND m.documento_id = %s
               ORDER BY m.id ASC""",
            (empresa_id, documento_id)
        ).fetchall()

    return jsonify({
        "ok": True,
        "movimientos": [
            {
                "id": m["id"],
                "fecha": str(m["fecha"]),
                "descripcion": m["descripcion"] or "",
                "tipo": m["tipo"] or "",
                "total": float(m["total"] or 0),
                "rfc_contraparte": m["rfc_contraparte"] or "",
                "referencia": m["referencia_bancaria"] or "",
                "numero_factura": m["numero_factura"] or "",
                "facturas_cfdi": m["facturas_cfdi"] or "",
                "estado": m["estado_clasificacion"] or "pendiente",
                "poliza_id": m["poliza_id"],
            }
            for m in movs
        ]
    })


@configuracion_bp.route("/empresas/<int:empresa_id>/movimientos/<int:movimiento_id>/editar", methods=["POST"])
@login_required
@empresa_requerida
def movimiento_editar(empresa_id, movimiento_id):
    """
    Permite editar directamente los valores leídos de un movimiento bancario
    (fecha, descripción, tipo ingreso/egreso, monto, RFC y referencia).
    """
    datos = request.get_json(silent=True) or {}
    fecha = datos.get("fecha")
    descripcion = (datos.get("descripcion") or "").strip()
    tipo = (datos.get("tipo") or "").strip().lower()
    total = datos.get("total")
    rfc = (datos.get("rfc_contraparte") or "").strip().upper()
    referencia = (datos.get("referencia") or "").strip()
    numero_factura = (datos.get("numero_factura") or "").strip()

    if not fecha or not descripcion or tipo not in ("ingreso", "egreso") or total is None:
        return jsonify({"ok": False, "error": "Datos incompletos o tipo inválido (debe ser ingreso o egreso)."}), 400

    try:
        total_float = float(total)
        if total_float < 0:
            return jsonify({"ok": False, "error": "El importe debe ser un valor positivo."}), 400
    except (ValueError, TypeError):
        return jsonify({"ok": False, "error": "Importe numérico inválido."}), 400

    with get_connection() as con:
        con.execute(
            """UPDATE movimientos
               SET fecha = %s, descripcion = %s, descripcion_normalizada = %s,
                   tipo = %s, total = %s, rfc_contraparte = %s, referencia_bancaria = %s,
                   numero_factura = %s
               WHERE id = %s AND empresa_id = %s""",
            (fecha, descripcion, descripcion.upper(), tipo, total_float, rfc or None, referencia or None, numero_factura, movimiento_id, empresa_id)
        )
        con.commit()

    return jsonify({"ok": True, "mensaje": "Movimiento actualizado exitosamente."})


# --- Explorador de CFDI y Asociación Manual de Facturas XML a Movimientos ---

@configuracion_bp.route("/empresas/<int:empresa_id>/cfdi/lista-json")
@login_required
@empresa_requerida
def cfdi_lista_json(empresa_id):
    """
    Devuelve la lista de facturas CFDI (XML) disponibles para vincular a movimientos,
    con búsqueda por texto, folio, UUID, RFC o razón social, y cálculo de saldo pendiente.
    """
    buscar = request.args.get("buscar", "").strip()
    rfc = request.args.get("rfc", "").strip().upper()
    tipo = request.args.get("tipo", "").strip()  # emitido | recibido
    movimiento_id = request.args.get("movimiento_id")

    with get_connection() as con:
        empresa = con.execute("SELECT rfc FROM empresas WHERE id = %s", (empresa_id,)).fetchone()
        rfc_empresa = (empresa["rfc"] if empresa else "") or ""

        where = ["c.empresa_id = %s"]
        params = [empresa_id]

        if rfc and rfc not in ("ND", "N/A", "NA", "XXX", "000000000000"):
            where.append("(c.rfc_emisor = %s OR c.rfc_receptor = %s)")
            params.extend([rfc, rfc])

        if tipo == "emitido":
            where.append("c.rfc_emisor = %s")
            params.append(rfc_empresa)
        elif tipo == "recibido":
            where.append("c.rfc_receptor = %s")
            params.append(rfc_empresa)

        if buscar:
            b_norm = buscar.replace("-", "").replace(" ", "").upper()
            p = f"%{buscar}%"
            p_norm = f"%{b_norm}%"
            where.append("""(
                c.folio ILIKE %s OR
                c.serie ILIKE %s OR
                c.uuid ILIKE %s OR
                REPLACE(c.uuid, '-', '') ILIKE %s OR
                c.rfc_emisor ILIKE %s OR
                c.nombre_emisor ILIKE %s OR
                c.rfc_receptor ILIKE %s OR
                c.nombre_receptor ILIKE %s
            )""")
            params.extend([p, p, p, p_norm, p, p, p, p])

        where_sql = " AND ".join(where)

        mov_id_int = int(movimiento_id) if (movimiento_id and str(movimiento_id).isdigit()) else -1
        sql = f"""
            SELECT c.id, c.uuid, c.serie, c.folio, c.fecha, c.total, c.subtotal,
                   c.rfc_emisor, c.nombre_emisor, c.rfc_receptor, c.nombre_receptor,
                   c.tipo_comprobante, c.moneda, c.archivo_origen,
                   COALESCE((SELECT SUM(cm.importe_aplicado) FROM cfdi_movimiento cm WHERE cm.cfdi_id = c.id AND cm.confirmado = 1), 0) AS aplicado_confirmado,
                   EXISTS(
                       SELECT 1 FROM cfdi_movimiento cm 
                       WHERE cm.cfdi_id = c.id AND cm.movimiento_id = %s AND cm.confirmado = 1
                   ) AS ya_asociado_a_este_mov
            FROM cfdis c
            WHERE {where_sql}
            ORDER BY c.fecha DESC, c.id DESC
            LIMIT 150
        """
        rows = con.execute(sql, tuple([mov_id_int] + params)).fetchall()

        resultado = []
        for r in rows:
            tot = float(r["total"] or 0.0)
            apl = float(r["aplicado_confirmado"] or 0.0)
            saldo = max(0.0, round(tot - apl, 2))
            contraparte_rfc = r["rfc_receptor"] if r["rfc_emisor"] == rfc_empresa else r["rfc_emisor"]
            contraparte_nombre = r["nombre_receptor"] if r["rfc_emisor"] == rfc_empresa else r["nombre_emisor"]
            resultado.append({
                "id": r["id"],
                "uuid": r["uuid"],
                "serie": r["serie"] or "",
                "folio": r["folio"] or "",
                "fecha": str(r["fecha"]),
                "total": tot,
                "aplicado": apl,
                "saldo": saldo,
                "contraparte_rfc": contraparte_rfc,
                "contraparte_nombre": contraparte_nombre or "",
                "tipo_comprobante": r["tipo_comprobante"] or "I",
                "moneda": r["moneda"] or "MXN",
                "ya_asociado": bool(r["ya_asociado_a_este_mov"]),
            })

    return jsonify({"ok": True, "cfdis": resultado})


@configuracion_bp.route("/empresas/<int:empresa_id>/cfdi/<int:cfdi_id>/detalle-json")
@login_required
@empresa_requerida
def cfdi_detalle_json(empresa_id, cfdi_id):
    """
    Devuelve los datos completos del XML de una factura para su visualización amigable en modal.
    """
    with get_connection() as con:
        c = con.execute("SELECT * FROM cfdis WHERE id = %s AND empresa_id = %s", (cfdi_id, empresa_id)).fetchone()
        if not c:
            return jsonify({"ok": False, "error": "CFDI no encontrado."}), 404

        impuestos = con.execute("SELECT * FROM cfdi_impuestos WHERE cfdi_id = %s", (cfdi_id,)).fetchall()
        movs = con.execute("""
            SELECT m.id, m.fecha, m.descripcion, m.total, cm.importe_aplicado, cm.motivo
            FROM cfdi_movimiento cm
            JOIN movimientos m ON m.id = cm.movimiento_id
            WHERE cm.cfdi_id = %s AND cm.confirmado = 1
        """, (cfdi_id,)).fetchall()

    impuestos_data = []
    for i in impuestos:
        impuestos_data.append({
            "base": float(i["base"] or 0.0),
            "importe": float(i["importe"] or 0.0),
            "tasa": float(i["tasa"] or 0.0),
            "tipo_factor": i.get("tipo_factor", "Tasa"),
            "es_retencion": bool(i["es_retencion"]),
            "tipo_impuesto": i["tipo_impuesto"] or "IVA",
        })

    movs_data = []
    for m in movs:
        movs_data.append({
            "id": m["id"],
            "fecha": str(m["fecha"]),
            "descripcion": m["descripcion"],
            "total": float(m["total"] or 0.0),
            "importe_aplicado": float(m["importe_aplicado"] or 0.0),
            "motivo": m["motivo"] or "",
        })

    return jsonify({
        "ok": True,
        "cfdi": {
            "id": c["id"],
            "uuid": c["uuid"],
            "serie": c["serie"] or "",
            "folio": c["folio"] or "",
            "fecha": str(c["fecha"]),
            "total": float(c["total"] or 0.0),
            "subtotal": float(c["subtotal"] or 0.0),
            "descuento": float(c.get("descuento") or 0.0),
            "moneda": c.get("moneda") or "MXN",
            "rfc_emisor": c["rfc_emisor"],
            "nombre_emisor": c["nombre_emisor"] or "",
            "rfc_receptor": c["rfc_receptor"],
            "nombre_receptor": c["nombre_receptor"] or "",
            "tipo_comprobante": c.get("tipo_comprobante") or "I",
            "metodo_pago": c.get("metodo_pago") or "",
            "archivo_origen": c.get("archivo_origen") or "",
            "impuestos": impuestos_data,
            "movimientos_asociados": movs_data,
        }
    })


@configuracion_bp.route("/empresas/<int:empresa_id>/movimientos/<int:movimiento_id>/cfdis-asociados-json")
@login_required
@empresa_requerida
def movimiento_cfdis_asociados_json(empresa_id, movimiento_id):
    """
    Devuelve la lista de facturas CFDI (XML) actualmente asociadas y confirmadas a un movimiento bancario.
    """
    with get_connection() as con:
        cur = con.cursor()
        empresa = cur.execute("SELECT rfc FROM empresas WHERE id = %s", (empresa_id,)).fetchone()
        rfc_empresa = (empresa["rfc"] if empresa else "") or ""

        rows = cur.execute(
            """SELECT c.id, c.uuid, c.serie, c.folio, c.fecha, c.total,
                      c.rfc_emisor, c.nombre_emisor, c.rfc_receptor, c.nombre_receptor,
                      cm.importe_aplicado
               FROM cfdi_movimiento cm
               JOIN cfdis c ON c.id = cm.cfdi_id
               WHERE cm.movimiento_id = %s AND cm.confirmado = 1
               ORDER BY c.fecha ASC, c.id ASC""",
            (movimiento_id,)
        ).fetchall()

        resultado = []
        cfdis_vistos = set()
        for r in rows:
            key_u = (r["uuid"] or "").strip().upper() or str(r["id"])
            if key_u in cfdis_vistos:
                continue
            cfdis_vistos.add(key_u)
            contraparte_rfc = r["rfc_receptor"] if r["rfc_emisor"] == rfc_empresa else r["rfc_emisor"]
            contraparte_nombre = r["nombre_receptor"] if r["rfc_emisor"] == rfc_empresa else r["nombre_emisor"]
            resultado.append({
                "id": r["id"],
                "uuid": r["uuid"],
                "serie": r["serie"] or "",
                "folio": r["folio"] or "",
                "fecha": str(r["fecha"]),
                "total": float(r["total"] or 0.0),
                "importe_aplicado": float(r["importe_aplicado"] or 0.0),
                "contraparte_rfc": contraparte_rfc or "",
                "contraparte_nombre": contraparte_nombre or "",
            })

    return jsonify({"ok": True, "cfdis": resultado})


@configuracion_bp.route("/empresas/<int:empresa_id>/movimientos/<int:movimiento_id>/asociar-cfdi", methods=["POST"])
@login_required
@empresa_requerida
def movimiento_asociar_cfdi(empresa_id, movimiento_id):
    """
    Asocia manualmente una o varias facturas CFDI (XML) seleccionadas por el usuario a un movimiento.
    Por defecto opera en modo 'agregar' para permitir asociar facturas de una en una sin reemplazar
    ni borrar las previamente vinculadas. Si se envía modo='reemplazar', actualiza la lista al conjunto enviado.
    Si se proporciona una cuenta_contraparte, la asigna y actualiza el asiento contable.
    """
    datos = request.get_json(silent=True) or {}
    cfdi_ids = datos.get("cfdi_ids") or []
    cuenta_contraparte = (datos.get("cuenta_contraparte") or "").strip()
    cuenta_anticipo = (datos.get("cuenta_anticipo") or "").strip()
    modo = (datos.get("modo") or "agregar").strip().lower()  # 'agregar' o 'reemplazar'

    if isinstance(cfdi_ids, (int, str)):
        cfdi_ids = [int(cfdi_ids)]
    cfdi_ids = list(dict.fromkeys(int(cid) for cid in cfdi_ids if str(cid).isdigit()))

    with get_connection() as con:
        cur = con.cursor()
        mov = cur.execute(
            "SELECT * FROM movimientos WHERE id = %s AND empresa_id = %s",
            (movimiento_id, empresa_id)
        ).fetchone()
        if not mov:
            return jsonify({"ok": False, "error": "Movimiento no encontrado."}), 404

        empresa = cur.execute("SELECT * FROM empresas WHERE id = %s", (empresa_id,)).fetchone()
        rfc_empresa = (empresa["rfc"] if empresa else "") or ""

        # En modo 'agregar', conservar las facturas que ya estaban confirmadas en este movimiento
        if modo != "reemplazar":
            existentes_res = cur.execute(
                "SELECT cfdi_id FROM cfdi_movimiento WHERE movimiento_id = %s AND confirmado = 1",
                (movimiento_id,)
            ).fetchall()
            ids_existentes = [r["cfdi_id"] for r in existentes_res]
            cfdi_ids = list(dict.fromkeys(ids_existentes + cfdi_ids))

        if not cfdi_ids:
            return jsonify({"ok": False, "error": "Selecciona al menos una factura CFDI para asociar."}), 400

        placeholders = ",".join(["%s"] * len(cfdi_ids))
        cfdis_raw = cur.execute(
            f"SELECT * FROM cfdis WHERE id IN ({placeholders}) AND empresa_id = %s ORDER BY fecha ASC, id ASC",
            tuple(cfdi_ids + [empresa_id])
        ).fetchall()

        if not cfdis_raw:
            return jsonify({"ok": False, "error": "No se encontraron las facturas indicadas."}), 404

        # Deduplicar por UUID o ID para no repetir la misma factura
        cfdis = []
        uuids_unicos = set()
        for c in cfdis_raw:
            key_u = (c["uuid"] or "").strip().upper() or str(c["id"])
            if key_u in uuids_unicos:
                continue
            uuids_unicos.add(key_u)
            cfdis.append(c)

        # Si se especificó explícitamente modo 'reemplazar', desvincular las que el usuario desmarcó
        if modo == "reemplazar":
            cfdi_ids_validos = [c["id"] for c in cfdis]
            if cfdi_ids_validos:
                placeholders_validos = ",".join(["%s"] * len(cfdi_ids_validos))
                cur.execute(
                    f"DELETE FROM cfdi_movimiento WHERE movimiento_id = %s AND cfdi_id NOT IN ({placeholders_validos})",
                    tuple([movimiento_id] + cfdi_ids_validos)
                )

        saldo_mov = float(mov["total"] or 0.0)
        grupo_id = uuid.uuid4().hex
        contraparte_rfc = None

        for c in cfdis:
            c_tot = float(c["total"] or 0.0)
            # Saldo disponible del CFDI excluyendo este movimiento
            apl_otro = cur.execute(
                "SELECT COALESCE(SUM(importe_aplicado), 0) FROM cfdi_movimiento WHERE cfdi_id = %s AND movimiento_id != %s AND confirmado = 1",
                (c["id"], movimiento_id)
            ).fetchone()[0] or 0.0
            saldo_cfdi = max(0.0, c_tot - float(apl_otro))

            if saldo_mov > 0:
                importe_aplicar = min(saldo_cfdi, saldo_mov) if saldo_cfdi > 0 else min(c_tot, saldo_mov)
            else:
                importe_aplicar = 0.0
            saldo_mov = max(0.0, saldo_mov - importe_aplicar)

            cur.execute(
                """INSERT INTO cfdi_movimiento
                   (grupo_id, cfdi_id, movimiento_id, importe_aplicado, tipo_match, confianza, motivo, confirmado)
                   VALUES (%s, %s, %s, %s, 'manual', 100, 'Asociación manual seleccionada por el usuario', 1)
                   ON CONFLICT (cfdi_id, movimiento_id) DO UPDATE SET
                    importe_aplicado = EXCLUDED.importe_aplicado,
                    confirmado = 1,
                    motivo = 'Asociación manual confirmada por el usuario'""",
                (grupo_id, c["id"], movimiento_id, importe_aplicar)
            )
            if not contraparte_rfc:
                contraparte_rfc = c["rfc_receptor"] if c["rfc_emisor"] == rfc_empresa else c["rfc_emisor"]

        todos_folios = cur.execute(
            """SELECT DISTINCT COALESCE(NULLIF(TRIM(COALESCE(c.serie, '') || COALESCE(c.folio, '')), ''), 'S/F') AS fol
               FROM cfdi_movimiento cm JOIN cfdis c ON c.id = cm.cfdi_id
               WHERE cm.movimiento_id = %s AND cm.confirmado = 1""",
            (movimiento_id,)
        ).fetchall()
        lista_folios = list(dict.fromkeys(r["fol"].strip() for r in todos_folios if r["fol"] and r["fol"].strip()))
        str_folios = ", ".join(lista_folios)

        cur.execute(
            """UPDATE movimientos
               SET numero_factura = %s,
                   rfc_contraparte = COALESCE(NULLIF(rfc_contraparte, ''), %s)
               WHERE id = %s AND empresa_id = %s""",
            (str_folios, contraparte_rfc, movimiento_id, empresa_id)
        )
        con.commit()

    # Detección de Anticipo: Si el CFDI es de mes posterior a la fecha del movimiento
    es_mes_posterior = any(str(c["fecha"])[:7] > str(mov["fecha"])[:7] for c in cfdis)
    if es_mes_posterior:
        tipo_ant = "cliente" if mov["tipo"] == "ingreso" else "proveedor"
        if not cuenta_anticipo:
            cta_cfg = empresa.get("cuenta_anticipo_clientes") if tipo_ant == "cliente" else empresa.get("cuenta_anticipo_proveedores")
            if cta_cfg:
                cuenta_anticipo = cta_cfg

        if not cuenta_anticipo:
            return jsonify({
                "ok": False,
                "requiere_cuenta_anticipo": True,
                "tipo_anticipo": tipo_ant,
                "fecha_movimiento": str(mov["fecha"]),
                "fecha_cfdi": str(cfdis[0]["fecha"]),
                "error": f"La factura seleccionada es de un mes posterior ({str(cfdis[0]['fecha'])[:7]} vs {str(mov['fecha'])[:7]}). Corresponde a un ANTICIPO. Por favor captura la cuenta de anticipo de {tipo_ant}s."
            }), 400

        from polizas_edicion import crear_polizas_anticipo
        res_ant = crear_polizas_anticipo(empresa_id, movimiento_id, cfdi_ids, cuenta_anticipo, cuenta_contraparte)
        return jsonify({
            "ok": True,
            "es_anticipo": True,
            "mensaje": f"{len(cfdis)} factura(s) de mes posterior asociadas como ANTICIPO. Se generaron 2 pólizas automáticamente (Anticipo #{res_ant['poliza_anticipo_id']} y Aplicación Diario #{res_ant['poliza_aplicacion_id']}) con subcuenta {res_ant.get('subcuenta_anticipo', '')} ({res_ant.get('desc_subcuenta', '')}).",
            "folios": str_folios,
            "asiento_guardado": True,
            "poliza_anticipo_id": res_ant["poliza_anticipo_id"],
            "poliza_aplicacion_id": res_ant["poliza_aplicacion_id"],
            "subcuenta_anticipo": res_ant.get("subcuenta_anticipo"),
            "desc_subcuenta": res_ant.get("desc_subcuenta"),
        })

    # Si se especificó una contra-cuenta, actualizar o generar el asiento contable
    asiento_guardado = False
    if cuenta_contraparte:
        try:
            asiento_actual = obtener_asiento_movimiento(empresa_id, movimiento_id)
            if asiento_actual and asiento_actual.get("lineas"):
                cta_clean = cuenta_contraparte.split(" - ")[0].split()[0].replace("-", "").replace(".", "").strip()
                lineas_nuevas = []
                for l in asiento_actual["lineas"]:
                    es_banco = "banco" in l.get("nombre_cuenta", "").lower() or str(l.get("cuenta", "")).startswith("1020")
                    if not es_banco:
                        lineas_nuevas.append({
                            "cuenta": cuenta_contraparte.split(" - ")[0].strip(),
                            "cargo": l["cargo"],
                            "abono": l["abono"],
                            "descripcion": l.get("descripcion") or mov["descripcion"] or ""
                        })
                    else:
                        lineas_nuevas.append(l)
                guardar_asiento_movimiento(empresa_id, movimiento_id, lineas_nuevas, concepto=mov["descripcion"])
                asiento_guardado = True
        except Exception:
            pass

    return jsonify({
        "ok": True,
        "mensaje": f"{len(cfdis)} factura(s) asociadas correctamente.",
        "folios": str_folios,
        "asiento_guardado": asiento_guardado,
        "requiere_contra_cuenta": not asiento_guardado and not bool(mov.get("poliza_id"))
    })


@configuracion_bp.route("/empresas/<int:empresa_id>/cfdi/sugerir-cuenta")
@login_required
@empresa_requerida
def cfdi_sugerir_cuenta(empresa_id):
    """
    Busca en el catálogo de cuentas de la empresa sugerencias para un cliente o proveedor
    a partir de su RFC o nombre.
    """
    rfc = request.args.get("rfc", "").strip().upper()
    nombre = request.args.get("nombre", "").strip()
    cuentas = []

    with get_connection() as con:
        cur = con.cursor()
        if rfc:
            cur.execute(
                """SELECT cuenta, descripcion FROM cuentas_catalogo
                   WHERE empresa_id = %s AND (descripcion ILIKE %s OR cuenta ILIKE %s)
                   ORDER BY cuenta LIMIT 5""",
                (empresa_id, f"%{rfc}%", f"%{rfc}%")
            )
            cuentas = cur.fetchall()

        if not cuentas and nombre:
            palabras = [p for p in nombre.split() if len(p) >= 4 and p.upper() not in ("S.A.", "DE", "C.V.", "S.A", "C.V", "SAPI", "S.C.", "LLC")]
            for p in palabras[:3]:
                cur.execute(
                    """SELECT cuenta, descripcion FROM cuentas_catalogo
                       WHERE empresa_id = %s AND descripcion ILIKE %s
                       ORDER BY cuenta LIMIT 5""",
                    (empresa_id, f"%{p}%")
                )
                res = cur.fetchall()
                if res:
                    cuentas = res
                    break

    return jsonify({
        "ok": True,
        "cuentas": [{"cuenta": c["cuenta"], "descripcion": c["descripcion"]} for c in cuentas]
    })


@configuracion_bp.route("/empresas/<int:empresa_id>/movimientos/<int:movimiento_id>/asiento-json")
@login_required
@empresa_requerida
def movimiento_asiento_json(empresa_id, movimiento_id):
    """
    Devuelve los datos del asiento contable completo de un movimiento con números y nombres de cuenta.
    Si el movimiento no tiene póliza aún, propone un asiento cuadrado inicial.
    """
    res = obtener_asiento_movimiento(empresa_id, movimiento_id)
    if not res:
        return jsonify({"ok": False, "error": "Movimiento no encontrado."}), 404
    return jsonify({"ok": True, **res})


@configuracion_bp.route("/empresas/<int:empresa_id>/movimientos/<int:movimiento_id>/guardar-asiento", methods=["POST"])
@login_required
@empresa_requerida
def movimiento_guardar_asiento_route(empresa_id, movimiento_id):
    """
    Guarda o edita el asiento contable manual de un movimiento, verificando cuadre exacto de cargos y abonos.
    """
    datos = request.get_json(silent=True) or {}
    lineas = datos.get("lineas") or []
    concepto = datos.get("concepto")

    try:
        resultado = guardar_asiento_movimiento(empresa_id, movimiento_id, lineas, concepto=concepto)
        return jsonify(resultado)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@configuracion_bp.route("/empresas/<int:empresa_id>/movimientos/<int:movimiento_id>/desasociar-cfdi", methods=["POST"])
@login_required
@empresa_requerida
def movimiento_desasociar_cfdi(empresa_id, movimiento_id):
    """
    Desvincula una factura CFDI (o todas) de un movimiento.
    """
    datos = request.get_json(silent=True) or {}
    cfdi_id = datos.get("cfdi_id")

    with get_connection() as con:
        cur = con.cursor()
        if cfdi_id and str(cfdi_id).isdigit():
            cur.execute("DELETE FROM cfdi_movimiento WHERE movimiento_id = %s AND cfdi_id = %s", (movimiento_id, int(cfdi_id)))
        else:
            cur.execute("DELETE FROM cfdi_movimiento WHERE movimiento_id = %s", (movimiento_id,))

        todos_folios = cur.execute(
            """SELECT DISTINCT COALESCE(c.serie, '') || COALESCE(c.folio, '') AS fol
               FROM cfdi_movimiento cm JOIN cfdis c ON c.id = cm.cfdi_id
               WHERE cm.movimiento_id = %s AND cm.confirmado = 1""",
            (movimiento_id,)
        ).fetchall()
        lista_folios = [r["fol"].strip() for r in todos_folios if r["fol"] and r["fol"].strip()]
        str_folios = ", ".join(lista_folios)

        cur.execute("UPDATE movimientos SET numero_factura = %s WHERE id = %s AND empresa_id = %s", (str_folios, movimiento_id, empresa_id))
        con.commit()

    return jsonify({"ok": True, "mensaje": "Asociación eliminada correctamente.", "folios": str_folios})


@configuracion_bp.route("/empresas/<int:empresa_id>/movimientos")
@login_required
@empresa_requerida
def movimientos_lista(empresa_id):
    """
    Pantalla completa para revisar y editar todos los movimientos bancarios importados,
    sus asientos contables (número y nombre de cuenta), cobertura de facturas CFDI y estados.
    """
    buscar = request.args.get("buscar", "").strip()
    estado = request.args.get("estado", "").strip()
    tipo = request.args.get("tipo", "").strip()
    doc_id = request.args.get("documento_id", "").strip()
    cobertura = request.args.get("cobertura", "").strip()
    pagina = max(1, int(request.args.get("pagina", 1)))
    por_pagina = 50

    with get_connection() as con:
        docs = con.execute(
            "SELECT id, nombre_archivo FROM documentos_importados WHERE empresa_id = %s ORDER BY id DESC",
            (empresa_id,)
        ).fetchall()
        bancos = con.execute("SELECT id, nombre, cuenta_contable FROM bancos WHERE empresa_id = %s", (empresa_id,)).fetchall()
        cuentas = catalogo_repo.listar(empresa_id)

        where_clauses = ["m.empresa_id = %s"]
        params = [empresa_id]

        if buscar:
            p = f"%{buscar}%"
            where_clauses.append("(m.descripcion ILIKE %s OR m.rfc_contraparte ILIKE %s OR m.numero_factura ILIKE %s OR m.referencia_bancaria ILIKE %s)")
            params.extend([p, p, p, p])

        if estado:
            where_clauses.append("m.estado_clasificacion = %s")
            params.append(estado)

        if tipo in ("ingreso", "egreso"):
            where_clauses.append("m.tipo = %s")
            params.append(tipo)

        if doc_id and doc_id.isdigit():
            where_clauses.append("m.documento_id = %s")
            params.append(int(doc_id))

        if cobertura == "cubierto_100":
            where_clauses.append(
                """EXISTS (SELECT 1 FROM cfdi_movimiento cm WHERE cm.movimiento_id = m.id AND cm.confirmado = 1)
                   AND ABS(m.total - COALESCE((SELECT SUM(c.total) FROM cfdi_movimiento cm JOIN cfdis c ON c.id = cm.cfdi_id WHERE cm.movimiento_id = m.id AND cm.confirmado = 1), 0)) <= 0.05"""
            )
        elif cobertura == "parcial":
            where_clauses.append(
                """EXISTS (SELECT 1 FROM cfdi_movimiento cm WHERE cm.movimiento_id = m.id AND cm.confirmado = 1)
                   AND COALESCE((SELECT SUM(c.total) FROM cfdi_movimiento cm JOIN cfdis c ON c.id = cm.cfdi_id WHERE cm.movimiento_id = m.id AND cm.confirmado = 1), 0) > 0
                   AND COALESCE((SELECT SUM(c.total) FROM cfdi_movimiento cm JOIN cfdis c ON c.id = cm.cfdi_id WHERE cm.movimiento_id = m.id AND cm.confirmado = 1), 0) < m.total - 0.05"""
            )
        elif cobertura == "sobrepasa":
            where_clauses.append(
                """EXISTS (SELECT 1 FROM cfdi_movimiento cm WHERE cm.movimiento_id = m.id AND cm.confirmado = 1)
                   AND COALESCE((SELECT SUM(c.total) FROM cfdi_movimiento cm JOIN cfdis c ON c.id = cm.cfdi_id WHERE cm.movimiento_id = m.id AND cm.confirmado = 1), 0) > m.total + 0.05"""
            )
        elif cobertura == "sin_facturas":
            where_clauses.append(
                "NOT EXISTS (SELECT 1 FROM cfdi_movimiento cm WHERE cm.movimiento_id = m.id AND cm.confirmado = 1)"
            )

        where_sql = " AND ".join(where_clauses)
        total_movimientos = con.execute(f"SELECT COUNT(*) FROM movimientos m WHERE {where_sql}", tuple(params)).fetchone()[0]

        offset = (pagina - 1) * por_pagina
        params_pag = list(params) + [por_pagina, offset]

        sql = f"""
            SELECT m.*, 
                   d.nombre_archivo,
                   p.numero AS poliza_numero,
                   p.tipo AS poliza_tipo,
                   p.cuadrada AS poliza_cuadrada,
                   p_ap.numero AS poliza_aplicacion_numero,
                   p_ap.tipo AS poliza_aplicacion_tipo,
                   (
                       SELECT string_agg(DISTINCT COALESCE(NULLIF(TRIM(COALESCE(c.serie, '') || COALESCE(c.folio, '')), ''), 'S/F'), ', ')
                       FROM cfdi_movimiento cm
                       JOIN cfdis c ON c.id = cm.cfdi_id
                       WHERE cm.movimiento_id = m.id AND cm.confirmado = 1
                   ) AS facturas_cfdi,
                   (
                       SELECT COUNT(*) FROM cfdi_movimiento cm WHERE cm.movimiento_id = m.id AND cm.confirmado = 1
                   ) AS total_cfdis_asociados,
                   (
                       SELECT COALESCE(SUM(c.total), 0)
                       FROM cfdi_movimiento cm
                       JOIN cfdis c ON c.id = cm.cfdi_id
                       WHERE cm.movimiento_id = m.id AND cm.confirmado = 1
                   ) AS total_cfdis_monto,
                   (
                       SELECT COALESCE(SUM(cm.importe_aplicado), 0)
                       FROM cfdi_movimiento cm
                       WHERE cm.movimiento_id = m.id AND cm.confirmado = 1
                   ) AS total_cfdis_aplicado
            FROM movimientos m
            LEFT JOIN documentos_importados d ON d.id = m.documento_id
            LEFT JOIN polizas p ON p.id = m.poliza_id
            LEFT JOIN polizas p_ap ON p_ap.id = m.poliza_aplicacion_id
            WHERE {where_sql}
            ORDER BY m.fecha DESC, m.id DESC
            LIMIT %s OFFSET %s
        """
        movimientos_filas = con.execute(sql, tuple(params_pag)).fetchall()
        movimientos = [dict(r) for r in movimientos_filas]

        # Cargar en bloque las líneas de asientos contables para los movimientos mostrados
        poliza_ids = list(set([m["poliza_id"] for m in movimientos if m.get("poliza_id")] + [m["poliza_aplicacion_id"] for m in movimientos if m.get("poliza_aplicacion_id")]))
        lineas_por_poliza = {}
        if poliza_ids:
            placeholders_pol = ",".join(["%s"] * len(poliza_ids))
            sql_lineas = f"""
                SELECT pl.poliza_id, pl.cuenta, cc.descripcion AS nombre_cuenta, pl.naturaleza, pl.importe, pl.descripcion
                FROM poliza_lineas pl
                LEFT JOIN cuentas_catalogo cc ON cc.empresa_id = %s AND (cc.cuenta = pl.cuenta OR REPLACE(cc.cuenta, '-', '') = REPLACE(pl.cuenta, '-', ''))
                WHERE pl.poliza_id IN ({placeholders_pol})
                ORDER BY pl.poliza_id, pl.orden ASC, pl.id ASC
            """
            filas_lineas = con.execute(sql_lineas, tuple([empresa_id] + poliza_ids)).fetchall()
            for fl in filas_lineas:
                pid = fl["poliza_id"]
                if pid not in lineas_por_poliza:
                    lineas_por_poliza[pid] = []
                lineas_por_poliza[pid].append({
                    "cuenta": fl["cuenta"],
                    "nombre_cuenta": fl["nombre_cuenta"] or "Cuenta no catalogada",
                    "naturaleza": fl["naturaleza"].lower(),
                    "importe": float(fl["importe"] or 0.0),
                    "descripcion": fl["descripcion"] or ""
                })

        # Calcular estados de cobertura de facturas y adjuntar líneas contables
        for m in movimientos:
            tot = float(m["total"] or 0.0)
            monto_cfdi = float(m.get("total_cfdis_monto") or 0.0)
            cant_cfdi = int(m.get("total_cfdis_asociados") or 0)
            diff = round(tot - monto_cfdi, 2)

            if cant_cfdi == 0:
                m["cobertura_badge"] = "sin_facturas"
                m["cobertura_texto"] = "Sin facturas"
                m["cobertura_detalle"] = f"Falta comprobar ${tot:,.2f}"
                m["cobertura_color"] = "bg-slate-100 text-slate-700 border-slate-300"
                m["cobertura_icono"] = "❌"
            elif abs(diff) <= 0.05:
                m["cobertura_badge"] = "cubierto_100"
                m["cobertura_texto"] = "Cubierto 100%"
                m["cobertura_detalle"] = f"${monto_cfdi:,.2f} de ${tot:,.2f}"
                m["cobertura_color"] = "bg-emerald-100 text-emerald-800 border-emerald-300"
                m["cobertura_icono"] = "✓"
            elif diff > 0.05:
                m["cobertura_badge"] = "parcial"
                m["cobertura_texto"] = "Parcial / Incompleto"
                m["cobertura_detalle"] = f"Cubierto ${monto_cfdi:,.2f} (Faltan ${diff:,.2f})"
                m["cobertura_color"] = "bg-amber-100 text-amber-800 border-amber-300"
                m["cobertura_icono"] = "⚠"
            else:
                m["cobertura_badge"] = "sobrepasa"
                m["cobertura_texto"] = "Sobrepasa movimiento"
                m["cobertura_detalle"] = f"Facturas: ${monto_cfdi:,.2f} (+${abs(diff):,.2f})"
                m["cobertura_color"] = "bg-blue-100 text-blue-800 border-blue-300"
                m["cobertura_icono"] = "ℹ"

            m["lineas_asiento"] = lineas_por_poliza.get(m.get("poliza_id"), [])

        resumen = con.execute("""
            SELECT 
                COUNT(*) AS total,
                COALESCE(SUM(total), 0) AS suma_total,
                COUNT(CASE WHEN estado_clasificacion IN ('pendiente', 'sugerido') THEN 1 END) AS pendientes,
                COUNT(CASE WHEN poliza_id IS NOT NULL THEN 1 END) AS con_poliza,
                COUNT(CASE WHEN EXISTS (SELECT 1 FROM cfdi_movimiento cm WHERE cm.movimiento_id = m.id AND cm.confirmado = 1) THEN 1 END) AS con_cfdi,
                COUNT(CASE WHEN EXISTS (SELECT 1 FROM cfdi_movimiento cm WHERE cm.movimiento_id = m.id AND cm.confirmado = 1)
                           AND ABS(m.total - COALESCE((SELECT SUM(c.total) FROM cfdi_movimiento cm2 JOIN cfdis c ON c.id = cm2.cfdi_id WHERE cm2.movimiento_id = m.id AND cm2.confirmado = 1), 0)) <= 0.05
                      THEN 1 END) AS cubiertos_100,
                COUNT(CASE WHEN EXISTS (SELECT 1 FROM cfdi_movimiento cm WHERE cm.movimiento_id = m.id AND cm.confirmado = 1)
                           AND COALESCE((SELECT SUM(c.total) FROM cfdi_movimiento cm2 JOIN cfdis c ON c.id = cm2.cfdi_id WHERE cm2.movimiento_id = m.id AND cm2.confirmado = 1), 0) > 0
                           AND COALESCE((SELECT SUM(c.total) FROM cfdi_movimiento cm2 JOIN cfdis c ON c.id = cm2.cfdi_id WHERE cm2.movimiento_id = m.id AND cm2.confirmado = 1), 0) < m.total - 0.05
                      THEN 1 END) AS parciales
            FROM movimientos m
            WHERE m.empresa_id = %s
        """, (empresa_id,)).fetchone()

    total_paginas = max(1, (total_movimientos + por_pagina - 1) // por_pagina)

    empresa = bancos_repo.obtener_empresa(empresa_id)
    return render_template(
        "movimientos.html",
        empresa=empresa,
        movimientos=movimientos,
        docs=docs,
        bancos=bancos,
        cuentas=cuentas,
        resumen=resumen,
        total_movimientos=total_movimientos,
        pagina=pagina,
        total_paginas=total_paginas,
        buscar=buscar,
        estado=estado,
        tipo=tipo,
        doc_id=doc_id,
        cobertura=cobertura,
    )

# --- Conciliaciones de CFDI sugeridas y confirmadas ---

@configuracion_bp.route("/empresas/<int:empresa_id>/conciliaciones")
@login_required
@empresa_requerida
def conciliaciones(empresa_id):
    propuestas = cfdi_repo.listar_propuestas_pendientes(empresa_id)
    confirmadas = cfdi_repo.listar_conciliaciones_confirmadas(empresa_id)
    with get_connection() as con:
        total_movs = con.execute("SELECT COUNT(*) FROM movimientos WHERE empresa_id = ?", (empresa_id,)).fetchone()[0]
        total_cfdis = con.execute("SELECT COUNT(*) FROM cfdis WHERE empresa_id = ?", (empresa_id,)).fetchone()[0]
    return render_template(
        "conciliaciones.html",
        propuestas=propuestas,
        confirmadas=confirmadas,
        total_movs=total_movs,
        total_cfdis=total_cfdis,
    )

@configuracion_bp.route("/empresas/<int:empresa_id>/conciliaciones/reconciliar", methods=["POST"])
@login_required
@empresa_requerida
def conciliaciones_reconciliar(empresa_id):
    res = cfdi_repo.conciliar_existentes(empresa_id)
    nuevos = len(res.get("conciliados", []))
    flash(f"Conciliación ejecutada: {nuevos} movimiento(s) asociado(s) con sus facturas CFDI.", "exito")
    return redirect(url_for("configuracion.conciliaciones", empresa_id=empresa_id))

@configuracion_bp.route("/empresas/<int:empresa_id>/conciliaciones/<grupo_id>/confirmar", methods=["POST"])
@login_required
@empresa_requerida
def conciliacion_confirmar(empresa_id, grupo_id):
    confirmada = cfdi_repo.confirmar_propuesta(grupo_id, empresa_id=empresa_id)
    if not confirmada:
        flash("Propuesta de conciliación no encontrada en esta empresa.", "error")
        return redirect(url_for("configuracion.conciliaciones", empresa_id=empresa_id)), 404
    flash("Conciliación confirmada.", "exito")
    return redirect(url_for("configuracion.conciliaciones", empresa_id=empresa_id))

@configuracion_bp.route("/empresas/<int:empresa_id>/conciliaciones/<grupo_id>/rechazar", methods=["POST"])
@login_required
@empresa_requerida
def conciliacion_rechazar(empresa_id, grupo_id):
    rechazada = cfdi_repo.rechazar_propuesta(grupo_id, empresa_id=empresa_id)
    if not rechazada:
        flash("Propuesta de conciliación no encontrada en esta empresa.", "error")
        return redirect(url_for("configuracion.conciliaciones", empresa_id=empresa_id)), 404
    flash("Propuesta descartada.", "exito")
    return redirect(url_for("configuracion.conciliaciones", empresa_id=empresa_id))

# --- Usuarios (multiusuario real) ---

@configuracion_bp.route("/empresas/<int:empresa_id>/usuarios", methods=["GET", "POST"])
@login_required
@empresa_requerida
def usuarios(empresa_id):
    if g.rol_empresa not in ("admin", "superadmin"):
        flash("Solo un administrador de esta empresa puede gestionar usuarios.", "error")
        return redirect(url_for("empresas.dashboard", empresa_id=empresa_id))

    if request.method == "POST":
        nombre_usuario = request.form["usuario"].strip().lower()
        rol_raw = request.form.get("rol", "editor")
        es_valido, resultado_rol = validar_rol_empresa_asignable(rol_raw)
        if not es_valido:
            flash(resultado_rol, "error")
            return redirect(url_for("configuracion.usuarios", empresa_id=empresa_id)), 400
        rol = resultado_rol
        organizacion_id = usuarios_repo.obtener_organizacion_de_empresa(empresa_id)

        if not nombre_usuario:
            flash("Escribe el nombre de usuario.", "error")
            return redirect(url_for("configuracion.usuarios", empresa_id=empresa_id))

        existente = usuarios_repo.buscar_usuario_por_usuario(nombre_usuario)
        if existente:
            if existente["organizacion_id"] != organizacion_id:
                flash(f"'{nombre_usuario}' pertenece a otra organización.", "error")
                return redirect(url_for("configuracion.usuarios", empresa_id=empresa_id))
            usuarios_repo.asignar_a_empresa(existente["id"], empresa_id, rol)
            flash(f"{nombre_usuario} agregado a esta empresa con rol '{rol}'.", "exito")
        else:
            nombre = request.form.get("nombre", "").strip()
            password = request.form.get("password", "")
            if not nombre or not password:
                flash(
                    f"'{nombre_usuario}' no existe todavía en tu organización. "
                    f"Captura nombre y contraseña para crearlo.", "error",
                )
                return redirect(url_for("configuracion.usuarios", empresa_id=empresa_id))
            correo = request.form.get("correo", "").strip().lower() or None
            nuevo_id = usuarios_repo.crear_usuario(organizacion_id, nombre, nombre_usuario, password, correo=correo)
            usuarios_repo.asignar_a_empresa(nuevo_id, empresa_id, rol)
            flash(f"Usuario {nombre_usuario} creado y agregado con rol '{rol}'.", "exito")

        return redirect(url_for("configuracion.usuarios", empresa_id=empresa_id))

    lista = usuarios_repo.listar_usuarios_de_empresa(empresa_id)
    return render_template("usuarios.html", lista=lista)

@configuracion_bp.route("/empresas/<int:empresa_id>/usuarios/<int:usuario_id>/quitar", methods=["POST"])
@login_required
@empresa_requerida
def usuario_quitar(empresa_id, usuario_id):
    if g.rol_empresa not in ("admin", "superadmin"):
        flash("Solo un administrador puede quitar usuarios.", "error")
        return redirect(url_for("empresas.dashboard", empresa_id=empresa_id))
    if usuario_id == g.usuario["id"]:
        flash("No puedes quitarte a ti mismo de la empresa.", "error")
        return redirect(url_for("configuracion.usuarios", empresa_id=empresa_id))
    quitado = usuarios_repo.quitar_de_empresa(usuario_id, empresa_id)
    if not quitado:
        flash("El usuario no estaba asignado a esta empresa.", "error")
        return redirect(url_for("configuracion.usuarios", empresa_id=empresa_id)), 404
    flash("Usuario quitado de esta empresa.", "exito")
    return redirect(url_for("configuracion.usuarios", empresa_id=empresa_id))
