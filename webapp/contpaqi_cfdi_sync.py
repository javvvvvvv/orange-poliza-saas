# ============================================================================
# PROPIEDAD INTELECTUAL Y LICENCIA COMERCIAL CERRADA
# ============================================================================
# Autor Legal y Titular de Derechos: JAVIER ILLAN GONZALEZ
# Organización: ORANGE CREW
# Contacto: ILLANJAVIER9@GMAIL.COM
# ============================================================================
import os
import io
import json
import time
import zipfile
import tempfile
import urllib.request
import urllib.parse
from datetime import datetime, date
from calendar import monthrange

from db import get_connection
import cfdi_repo

CONTPAQI_ADD_HOST = os.environ.get("CONTPAQI_ADD_HOST", "25.39.115.45")
CONTPAQI_ADD_PORT = os.environ.get("CONTPAQI_ADD_PORT", "9080")
ADD_BASE_URL = f"http://{CONTPAQI_ADD_HOST}:{CONTPAQI_ADD_PORT}/saci/storageRestServices/addapi"

PROGRESO_DIR = os.path.join(tempfile.gettempdir(), "orange_sync_cfdi")
os.makedirs(PROGRESO_DIR, exist_ok=True)


def _ruta_progreso(empresa_id: int) -> str:
    return os.path.join(PROGRESO_DIR, f"progreso_{empresa_id}.json")


def obtener_progreso_sync(empresa_id: int) -> dict:
    ruta = _ruta_progreso(empresa_id)
    if not os.path.exists(ruta):
        return {
            "estado": "inactivo",
            "porcentaje": 0,
            "etapa": "",
            "total": 0,
            "procesados": 0,
            "nuevos": 0,
            "omitidos": 0,
            "conciliados": 0,
            "error": None,
        }
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"estado": "error", "error": "No se pudo leer el archivo de progreso."}


def _guardar_progreso(empresa_id: int, estado: str, porcentaje: int, etapa: str,
                      total: int = 0, procesados: int = 0, nuevos: int = 0,
                      omitidos: int = 0, conciliados: int = 0, error: str = None,
                      detalles: dict = None):
    data = {
        "estado": estado,
        "porcentaje": max(0, min(100, porcentaje)),
        "etapa": etapa,
        "total": total,
        "procesados": procesados,
        "nuevos": nuevos,
        "omitidos": omitidos,
        "conciliados": conciliados,
        "error": error,
        "detalles": detalles or {},
        "actualizado_utc": datetime.utcnow().isoformat(),
    }
    ruta = _ruta_progreso(empresa_id)
    temp_ruta = ruta + ".tmp"
    try:
        with open(temp_ruta, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(temp_ruta, ruta)
    except Exception:
        pass


def _resolver_fechas(periodo: str, fecha_inicio: str = None, fecha_fin: str = None):
    """
    Regresa una tupla (f_ini_ddmmyyyy, f_fin_ddmmyyyy) o None si es 'todo'.
    """
    hoy = date.today()
    if periodo == "mes_actual":
        primer_dia = hoy.replace(day=1)
        _, ultimo = monthrange(hoy.year, hoy.month)
        ultimo_dia = hoy.replace(day=ultimo)
        return primer_dia.strftime("%d/%m/%Y"), ultimo_dia.strftime("%d/%m/%Y")

    elif periodo == "mes_anterior":
        primer_dia_este_mes = hoy.replace(day=1)
        # un día antes es el último día del mes pasado
        import datetime as dt
        ultimo_dia_mes_pasado = primer_dia_este_mes - dt.timedelta(days=1)
        primer_dia_mes_pasado = ultimo_dia_mes_pasado.replace(day=1)
        return primer_dia_mes_pasado.strftime("%d/%m/%Y"), ultimo_dia_mes_pasado.strftime("%d/%m/%Y")

    elif periodo == "anio_actual":
        return f"01/01/{hoy.year}", f"31/12/{hoy.year}"

    elif periodo == "todo":
        return None

    elif periodo == "custom" and fecha_inicio and fecha_fin:
        try:
            d_ini = datetime.strptime(fecha_inicio[:10], "%Y-%m-%d").date()
            d_fin = datetime.strptime(fecha_fin[:10], "%Y-%m-%d").date()
            return d_ini.strftime("%d/%m/%Y"), d_fin.strftime("%d/%m/%Y")
        except Exception:
            raise ValueError(f"Fechas personalizadas inválidas: {fecha_inicio} - {fecha_fin}")

    return None


def sincronizar_cfdis_desde_contpaqi(empresa_id: int, periodo: str = "mes_actual",
                                     fecha_inicio: str = None, fecha_fin: str = None,
                                     tipo_filtro: str = "todos"):
    """
    Descarga y sincroniza CFDIs directamente desde el ADD de CONTPAQi.
    Se ejecuta en segundo plano o de forma síncrona según convenga.
    """
    t_inicio = time.time()
    _guardar_progreso(empresa_id, "procesando", 5, "Conectando con CONTPAQi ADD...")

    # 1. Obtener datos de la empresa (guid_dsl y rfc)
    with get_connection() as con:
        empresa = con.execute(
            "SELECT id, nombre, rfc, base_datos_contpaqi, guid_dsl FROM empresas WHERE id = %s",
            (empresa_id,)
        ).fetchone()

    if not empresa:
        _guardar_progreso(empresa_id, "error", 0, "Empresa no encontrada", error="Empresa inexistente")
        return {"ok": False, "error": "Empresa inexistente"}

    guid_dsl = empresa.get("guid_dsl")
    if not guid_dsl:
        # Intentar buscarlo si tiene base_datos_contpaqi conocida
        bd = empresa.get("base_datos_contpaqi")
        mapeo_conocido = {
            "ctHERRAJES": "babbf85d-6389-43fd-9720-449d018fe64e",
            "ctANDREA_ARAGON_RAMIREZ": "8684e66d-3039-484c-a62b-8e9ce579db7a",
            "ctGrupo_Sanem_Medicamentos_SA_de_C": "e81eafbd-ee13-4104-8e0c-ad7dc207c2a2",
        }
        if bd in mapeo_conocido:
            guid_dsl = mapeo_conocido[bd]
            with get_connection() as con:
                con.execute("UPDATE empresas SET guid_dsl = %s WHERE id = %s", (guid_dsl, empresa_id))
                con.commit()
        else:
            msg = f"La empresa '{empresa['nombre']}' no tiene configurado el GuidDSL de CONTPAQi ADD."
            _guardar_progreso(empresa_id, "error", 0, "Falta GuidDSL", error=msg)
            return {"ok": False, "error": msg}

    rfc_empresa = (empresa.get("rfc") or "").strip().upper()

    # 2. Construir queryset según fechas
    try:
        rango_fechas = _resolver_fechas(periodo, fecha_inicio, fecha_fin)
    except Exception as e:
        _guardar_progreso(empresa_id, "error", 0, "Error en fechas", error=str(e))
        return {"ok": False, "error": str(e)}

    queryset_filtros = []
    if rango_fechas:
        f_ini, f_fin = rango_fechas
        queryset_filtros.append({
            "C": "Fecha",
            "O": 2,  # DateRange
            "I": 0,
            "V": [f_ini, f_fin]
        })

    qs_param = urllib.parse.quote(json.dumps(queryset_filtros))

    # 3. Consultar metadata paginada en el ADD
    _guardar_progreso(empresa_id, "procesando", 15, "Consultando índice de documentos en CONTPAQi...")

    pagina = 1
    rpp = 100
    documentos_add = []
    total_items = 0

    while True:
        url = (
            f"{ADD_BASE_URL}/documentos/datas/{guid_dsl}"
            f"?columnas=GuidDocument,UUID,Fecha,Total,TipoComprobante,RFCEmisor,RFCReceptor"
            f"&queryset={qs_param}&pag={pagina}&rpp={rpp}&tipodoc=document"
        )
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "OrangePoliza/2.0"})
            with urllib.request.urlopen(req, timeout=25) as resp:
                data_resp = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            msg_err = f"Error al comunicarse con CONTPAQi ADD ({url}): {e}"
            _guardar_progreso(empresa_id, "error", 0, "Error de conexión con ADD", error=msg_err)
            return {"ok": False, "error": msg_err}

        if pagina == 1:
            total_items = data_resp.get("totalItems", 0) or len(data_resp.get("data", []))

        items_pagina = data_resp.get("data", [])
        if not items_pagina:
            break

        documentos_add.extend(items_pagina)
        if len(documentos_add) >= total_items or len(items_pagina) < rpp:
            break
        pagina += 1

    total_encontrados = len(documentos_add)
    if total_encontrados == 0:
        _guardar_progreso(
            empresa_id, "completado", 100,
            "No se encontraron comprobantes en el periodo seleccionado.",
            total=0, procesados=0, nuevos=0, omitidos=0, conciliados=0
        )
        return {"ok": True, "total": 0, "nuevos": 0, "omitidos": 0, "conciliados": 0}

    # 4. Deduplicación inteligente: consultar UUIDs ya registrados
    _guardar_progreso(
        empresa_id, "procesando", 30,
        f"Analizando {total_encontrados} comprobantes contra base de datos...",
        total=total_encontrados
    )

    with get_connection() as con:
        cur = con.cursor()
        cur.execute("SELECT uuid FROM cfdis WHERE empresa_id = %s", (empresa_id,))
        uuids_existentes = set((r[0] or "").strip().lower() for r in cur.fetchall())

    docs_por_descargar = []
    omitidos = 0

    for doc in documentos_add:
        uuid_doc = (doc.get("uuid") or "").strip().lower()
        if uuid_doc and uuid_doc in uuids_existentes:
            omitidos += 1
        else:
            docs_por_descargar.append(doc)

    total_nuevos = len(docs_por_descargar)
    if total_nuevos == 0:
        _guardar_progreso(
            empresa_id, "completado", 100,
            f"Todos los {total_encontrados} comprobantes ya se encontraban importados en el sistema.",
            total=total_encontrados, procesados=total_encontrados,
            nuevos=0, omitidos=omitidos, conciliados=0
        )
        return {
            "ok": True,
            "total": total_encontrados,
            "nuevos": 0,
            "omitidos": omitidos,
            "conciliados": 0,
            "mensaje": "Todos los comprobantes ya estaban importados."
        }

    # 5. Descarga por lotes (batches de 50) y parseo/inserción
    tamano_lote = 50
    lotes = [docs_por_descargar[i:i + tamano_lote] for i in range(0, total_nuevos, tamano_lote)]
    total_lotes = len(lotes)

    nuevos_importados = 0
    total_conciliados = 0
    errores_importacion = []

    for num_lote, lote in enumerate(lotes, 1):
        guids_lote = [d["guiddocument"] for d in lote if d.get("guiddocument")]
        if not guids_lote:
            continue

        pct = 30 + int((num_lote / total_lotes) * 55)
        _guardar_progreso(
            empresa_id, "procesando", pct,
            f"Descargando e importando lote {num_lote}/{total_lotes} ({len(guids_lote)} XMLs)...",
            total=total_encontrados,
            procesados=omitidos + nuevos_importados,
            nuevos=nuevos_importados,
            omitidos=omitidos,
            conciliados=total_conciliados,
        )

        zip_url = f"{ADD_BASE_URL}/exporta/zip/{guid_dsl}?guids=" + ",".join(guids_lote)
        temp_lote_dir = tempfile.mkdtemp(prefix=f"orange_add_batch_{num_lote}_")
        rutas_xml_lote = []

        try:
            req_zip = urllib.request.Request(zip_url, headers={"User-Agent": "OrangePoliza/2.0"})
            with urllib.request.urlopen(req_zip, timeout=60) as resp_zip:
                zip_bytes = resp_zip.read()

            zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
            for item_name in zf.namelist():
                if item_name.lower().endswith(".xml"):
                    ruta_ext = zf.extract(item_name, temp_lote_dir)
                    rutas_xml_lote.append(ruta_ext)

            if rutas_xml_lote:
                # Importar usando cfdi_repo
                res_imp = cfdi_repo.importar_y_conciliar(
                    empresa_id,
                    rutas_xml_lote,
                    rfc_empresa,
                    tipo_hint=None if tipo_filtro == "todos" else tipo_filtro
                )
                nuevos_importados += res_imp.get("importados", 0)
                total_conciliados += len(res_imp.get("conciliados", []))
                if res_imp.get("errores"):
                    errores_importacion.extend(res_imp["errores"])

        except Exception as ex_lote:
            errores_importacion.append({"lote": num_lote, "error": str(ex_lote)})
        finally:
            import shutil
            shutil.rmtree(temp_lote_dir, ignore_errors=True)

    # 6. Reconciliación global final con movimientos bancarios existentes
    _guardar_progreso(
        empresa_id, "procesando", 92,
        "Conciliando automáticamente facturas con movimientos bancarios...",
        total=total_encontrados,
        procesados=total_encontrados,
        nuevos=nuevos_importados,
        omitidos=omitidos,
        conciliados=total_conciliados
    )

    try:
        res_reconcil = cfdi_repo.conciliar_existentes(empresa_id)
        if isinstance(res_reconcil, (int, float)):
            total_conciliados += int(res_reconcil)
    except Exception:
        pass

    tiempo_total = round(time.time() - t_inicio, 1)

    _guardar_progreso(
        empresa_id, "completado", 100,
        f"¡Sincronización finalizada con éxito en {tiempo_total}s! "
        f"{nuevos_importados} nuevos CFDI importados, {omitidos} omitidos por ya existir, "
        f"{total_conciliados} conciliados.",
        total=total_encontrados,
        procesados=total_encontrados,
        nuevos=nuevos_importados,
        omitidos=omitidos,
        conciliados=total_conciliados,
        detalles={
            "tiempo_segundos": tiempo_total,
            "errores": errores_importacion[:10],
        }
    )

    return {
        "ok": True,
        "total": total_encontrados,
        "nuevos": nuevos_importados,
        "omitidos": omitidos,
        "conciliados": total_conciliados,
        "tiempo_segundos": tiempo_total,
        "errores": errores_importacion,
    }
