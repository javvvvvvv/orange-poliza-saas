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
import hashlib
from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for, send_file, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash

from auth import login_required, empresa_requerida, obtener_empresas_del_usuario
from db import get_connection, hay_usuarios
from upload_security import (
    ruta_segura_en_directorio,
    extraer_xmls_de_zip_seguro,
    limpiar_temporales_antiguos,
    ErrorArchivoInseguro,
    EXTENSIONES_ESTADO_CUENTA,
    EXTENSIONES_CFDI,
)
import usuarios_repo
import bancos_repo
import catalogo_repo
import cfdi_repo
import marcado_repo
import movimientos_repo
import reglas_generales_repo
from rules_repository import RepositorioReglas

import pdf_importer
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

importacion_bp = Blueprint('importacion', __name__)

# --- Importar estado de cuenta y CFDI ---

@importacion_bp.route("/empresas/<int:empresa_id>/importar")
@login_required
@empresa_requerida
def importar(empresa_id):
    bancos = bancos_repo.listar_bancos(empresa_id)
    return render_template("importar.html", bancos=bancos)

@importacion_bp.route("/empresas/<int:empresa_id>/importar/vista-previa", methods=["GET", "POST"])
@login_required
@empresa_requerida
def importar_vista_previa(empresa_id):
    if request.method == "POST":
        limpiar_temporales_antiguos(os.path.join(UPLOADS_DIR, "_tmp"))
        archivos = [a for a in request.files.getlist("archivos_excel") if a and a.filename]
        if not archivos:
            flash("Selecciona uno o varios archivos Excel o PDF.", "error")
            return redirect(url_for("importacion.importar", empresa_id=empresa_id))

        token = uuid.uuid4().hex
        carpeta_temporal = os.path.join(UPLOADS_DIR, "_tmp", token)
        os.makedirs(carpeta_temporal, exist_ok=True)

        rutas = []
        try:
            for archivo in archivos:
                ruta = ruta_segura_en_directorio(
                    carpeta_temporal,
                    archivo.filename,
                    extensiones_permitidas=EXTENSIONES_ESTADO_CUENTA,
                )
                archivo.save(ruta)
                rutas.append(ruta)
        except ErrorArchivoInseguro as e:
            shutil.rmtree(carpeta_temporal, ignore_errors=True)
            flash(str(e), "error")
            return redirect(url_for("importacion.importar", empresa_id=empresa_id))

        session[f"preview_{token}"] = {
            "archivos": rutas,
            "banco_id": request.form.get("banco_id") or None,
        }
        hoja = None
        fila_encabezado = None
    else:
        token = request.args.get("token")
        hoja = request.args.get("hoja")
        fila_encabezado = request.args.get("fila_encabezado")
        fila_encabezado = int(fila_encabezado) if fila_encabezado is not None else None
        if not token or f"preview_{token}" not in session:
            flash("La vista previa expiró, vuelve a seleccionar el archivo.", "error")
            return redirect(url_for("importacion.importar", empresa_id=empresa_id))

    datos = session[f"preview_{token}"]
    primer_archivo = datos["archivos"][0]

    try:
        hojas = pdf_importer.listar_hojas(primer_archivo) if primer_archivo.lower().endswith('.pdf') else listar_hojas(primer_archivo)
        hoja_actual = hoja or hojas[0]
        crudo = pdf_importer.vista_previa_cruda(primer_archivo, nombre_hoja=hoja_actual, filas=20) if primer_archivo.lower().endswith('.pdf') else vista_previa_cruda(primer_archivo, nombre_hoja=hoja_actual, filas=20)
    except Exception as e:
        session.pop(f"preview_{token}", None)
        shutil.rmtree(os.path.join(UPLOADS_DIR, "_tmp", str(token)), ignore_errors=True)
        flash(f"No se pudo leer el archivo: {e}", "error")
        return redirect(url_for("importacion.importar", empresa_id=empresa_id))

    # Si el usuario no eligió explícitamente una fila (recién subió el
    # archivo, o acaba de cambiar de pestaña), se adivina automáticamente
    # dónde está el encabezado real, saltando membrete/logo/resumen -
    # así casi nunca hay que hacer clic manual en "Usar esta fila". Sigue
    # siendo 100% editable: cualquier clic explícito en la tabla de abajo
    # tiene prioridad y desactiva el aviso de "detectado automáticamente".
    fila_autodetectada = False
    if fila_encabezado is None:
        try:
            if primer_archivo.lower().endswith('.pdf'):
                fila_encabezado, sugerencias = pdf_importer.detectar_fila_encabezado_pdf(primer_archivo)
            else:
                fila_encabezado, sugerencias = detectar_fila_encabezado(primer_archivo, nombre_hoja=hoja_actual)
            fila_autodetectada = True
        except Exception:
            fila_encabezado = None

    columnas = filas_mapeadas = None
    if fila_encabezado is not None:
        try:
            preview = pdf_importer.vista_previa(primer_archivo, nombre_hoja=hoja_actual, fila_encabezado=fila_encabezado) if primer_archivo.lower().endswith('.pdf') else vista_previa(primer_archivo, nombre_hoja=hoja_actual, fila_encabezado=fila_encabezado)
            columnas = preview["columnas"]
            filas_mapeadas = preview["filas"]
        except Exception as e:
            flash(f"No se pudo usar esa fila como encabezado: {e}", "error")

    return render_template(
        "importar_preview.html",
        token=token, hojas=hojas, sugerencias=sugerencias if fila_autodetectada else {}, hoja_actual=hoja_actual,
        filas_crudas=crudo["filas"], total_columnas=crudo["total_columnas"],
        fila_encabezado=fila_encabezado, fila_autodetectada=fila_autodetectada,
        columnas=columnas, filas_mapeadas=filas_mapeadas,
        nombre_archivo=os.path.basename(primer_archivo),
        total_archivos=len(datos["archivos"]),
        formato_columnas=FORMATO_COLUMNAS_SEPARADAS,
        formato_signo=FORMATO_COLUMNA_CON_SIGNO,
        formato_tipo=FORMATO_VALOR_ABSOLUTO_Y_TIPO,
    )

@importacion_bp.route("/empresas/<int:empresa_id>/importar/confirmar", methods=["POST"])
@login_required
@empresa_requerida
def importar_confirmar(empresa_id):
    token = request.form.get("token")
    if not token or f"preview_{token}" not in session:
        flash("La vista previa expiró, vuelve a seleccionar el archivo.", "error")
        return redirect(url_for("importacion.importar", empresa_id=empresa_id))

    datos = session.pop(f"preview_{token}")
    banco_id = datos["banco_id"]
    if banco_id:
        with get_connection() as con:
            banco_valido = con.execute(
                "SELECT id FROM bancos WHERE id = ? AND empresa_id = ?",
                (banco_id, empresa_id),
            ).fetchone()
        if not banco_valido:
            banco_id = None
    nombre_hoja = request.form.get("nombre_hoja") or None

    def _vacio_a_none(valor):
        return valor if valor else None

    config = ConfiguracionImportacion(
        formato=request.form["formato"],
        columna_fecha=request.form["columna_fecha"],
        columna_ingresos=_vacio_a_none(request.form.get("columna_ingresos")),
        columna_egresos=_vacio_a_none(request.form.get("columna_egresos")),
        columna_importe=_vacio_a_none(request.form.get("columna_importe")),
        columna_tipo=_vacio_a_none(request.form.get("columna_tipo")),
        columnas_descripcion=[c for c in request.form.getlist("columnas_descripcion") if c],
        columnas_descripcion_ingresos=[c for c in request.form.getlist("columnas_descripcion_ingresos") if c],
        columnas_descripcion_egresos=[c for c in request.form.getlist("columnas_descripcion_egresos") if c],
        columna_rfc_contraparte=_vacio_a_none(request.form.get("columna_rfc")),
        columna_rfc_ingresos=_vacio_a_none(request.form.get("columna_rfc_ingresos")),
        columna_rfc_egresos=_vacio_a_none(request.form.get("columna_rfc_egresos")),
        columna_referencia=_vacio_a_none(request.form.get("columna_referencia")),
        columna_numero_factura=_vacio_a_none(request.form.get("columna_factura")),
        fila_encabezado=int(request.form.get("fila_encabezado", 0)),
    )

    carpeta_empresa = os.path.join(UPLOADS_DIR, str(empresa_id))
    os.makedirs(carpeta_empresa, exist_ok=True)

    total_importados = total_automaticos = total_pendientes = 0
    errores = []
    todos_descuadres = []

    for ruta_temporal in datos["archivos"]:
        with open(ruta_temporal, "rb") as f_hash:
            hash_sha256 = hashlib.sha256(f_hash.read()).hexdigest()

        with get_connection() as con_chk:
            ya_importado = con_chk.execute(
                "SELECT id, nombre_archivo FROM documentos_importados WHERE empresa_id = ? AND hash_sha256 = ?",
                (empresa_id, hash_sha256),
            ).fetchone()
        if ya_importado:
            errores.append(f"El archivo '{os.path.basename(ruta_temporal)}' ya fue importado previamente (documento #{ya_importado['id']}).")
            continue

        ruta_final = ruta_segura_en_directorio(
            carpeta_empresa,
            os.path.basename(ruta_temporal),
            extensiones_permitidas=EXTENSIONES_ESTADO_CUENTA,
        )
        _mover_archivo_con_reintentos(ruta_temporal, ruta_final)

        try:
            if ruta_final.lower().endswith(".pdf"):
                resultado = pdf_importer.importar_pdf(ruta_final, config, nombre_hoja=nombre_hoja)
            else:
                resultado = importar_excel(ruta_final, config, nombre_hoja=nombre_hoja)
        except Exception as e:
            errores.append(f"{os.path.basename(ruta_final)}: {e}")
            continue

        con = get_connection()
        cur = con.cursor()
        cur.execute(
            """INSERT INTO documentos_importados
               (empresa_id, banco_id, nombre_archivo, hash_sha256, ruta_archivo, nombre_hoja,
                tipo_archivo, importado_por, estado)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'procesado')""",
            (empresa_id, banco_id, os.path.basename(ruta_final), hash_sha256, ruta_final, nombre_hoja, 'pdf' if ruta_final.lower().endswith('.pdf') else 'excel', g.usuario["id"]),
        )
        documento_id = cur.lastrowid
        con.commit()
        con.close()

        ids_insertados = movimientos_repo.insertar_movimientos(empresa_id, documento_id, resultado["movimientos"])
        total_duplicados = getattr(ids_insertados, "duplicados", 0)
        try:
            from asociacion_repo import asociar_automaticamente
            asociar_automaticamente(empresa_id)
        except Exception:
            pass
        try:
            cfdi_repo.conciliar_existentes(empresa_id)
        except Exception:
            pass
        clasificacion = movimientos_repo.clasificar_pendientes(empresa_id)

        total_importados += resultado["filas_importadas"]
        total_automaticos += clasificacion["automaticos"]
        total_pendientes += clasificacion["pendientes_restantes"]
        todos_descuadres.extend(clasificacion.get("descuadres") or [])

    carpeta_temporal = os.path.join(UPLOADS_DIR, "_tmp", token)
    shutil.rmtree(carpeta_temporal, ignore_errors=True)

    msg_dup = f" ({total_duplicados} omitidos por ya existir)" if total_duplicados > 0 else ""
    flash(
        f"{total_importados} movimientos procesados{msg_dup}. {total_automaticos} clasificados "
        f"automáticamente, {total_pendientes} pendientes de revisión.",
        "exito",
    )
    if todos_descuadres:
        ejemplos = " | ".join(d["mensaje"] for d in todos_descuadres[:3])
        extra = f" (+{len(todos_descuadres) - 3} más)" if len(todos_descuadres) > 3 else ""
        flash(
            f"Atención: {len(todos_descuadres)} movimiento(s) coincidieron con una regla pero quedaron pendientes por descuadre: {ejemplos}{extra}",
            "error",
        )
    for err in errores:
        flash(err, "error")

    if total_pendientes > 0:
        return redirect(url_for("reglas.pendientes_siguiente", empresa_id=empresa_id))
    return redirect(url_for("empresas.dashboard", empresa_id=empresa_id))

@importacion_bp.route("/empresas/<int:empresa_id>/importar-cfdi", methods=["POST"])
@login_required
@empresa_requerida
def importar_cfdi_ruta(empresa_id):
    archivos = [a for a in request.files.getlist("archivos_cfdi") if a and a.filename]
    if not archivos:
        flash("Selecciona uno o varios archivos XML o un archivo ZIP.", "error")
        return redirect(url_for("importacion.importar", empresa_id=empresa_id))

    temp_cfdi_dir = tempfile.mkdtemp(prefix="orange_cfdi_")
    rutas = []
    try:
        try:
            for idx, archivo in enumerate(archivos):
                ruta_guardada = ruta_segura_en_directorio(
                    temp_cfdi_dir,
                    archivo.filename,
                    extensiones_permitidas=EXTENSIONES_CFDI,
                    prefijo_unico=True,
                )
                archivo.save(ruta_guardada)
                if ruta_guardada.lower().endswith(".zip"):
                    extraer_dir = os.path.join(temp_cfdi_dir, f"zip_{idx}_{uuid.uuid4().hex[:8]}")
                    xmls = extraer_xmls_de_zip_seguro(ruta_guardada, extraer_dir)
                    rutas.extend(xmls)
                    try:
                        os.remove(ruta_guardada)
                    except OSError:
                        pass
                else:
                    rutas.append(ruta_guardada)
        except ErrorArchivoInseguro as e:
            flash(str(e), "error")
            return redirect(url_for("importacion.importar", empresa_id=empresa_id))

        with get_connection() as con:
            empresa = con.execute("SELECT rfc FROM empresas WHERE id = ?", (empresa_id,)).fetchone()

        tipo_hint = request.form.get("tipo_hint")  # 'emitido' | 'recibido' | None
        resultado = cfdi_repo.importar_y_conciliar(
            empresa_id,
            rutas,
            (empresa["rfc"] if empresa else "") or "",
            tipo_hint=tipo_hint,
        )
    finally:
        shutil.rmtree(temp_cfdi_dir, ignore_errors=True)

    flash(
        f"{resultado['importados']} CFDI importados. "
        f"{len(resultado['conciliados'])} conciliados automáticamente (RFC + importe exactos). "
        f"{resultado['pendientes_revision']} propuestas de conciliación necesitan tu confirmación "
        f"(pagos parciales, facturas combinadas, etc.).",
        "exito",
    )
    for err in resultado["errores"]:
        flash(f"{err['archivo']}: {err['error']}", "error")

    if resultado["pendientes_revision"] > 0:
        return redirect(url_for("configuracion.conciliaciones", empresa_id=empresa_id))
    return redirect(url_for("empresas.dashboard", empresa_id=empresa_id))

