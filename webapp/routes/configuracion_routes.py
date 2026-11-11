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
from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for, send_file, send_from_directory
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
