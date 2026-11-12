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
from itertools import zip_longest
from flask import Blueprint, flash, g, redirect, render_template, request, session, url_for, send_file, send_from_directory
from werkzeug.security import check_password_hash, generate_password_hash

from auth import login_required, empresa_requerida, obtener_empresas_del_usuario
from db import get_connection, hay_usuarios
import usuarios_repo
import bancos_repo
import catalogo_repo
import cfdi_repo
import marcado_repo
import movimientos_repo
import reglas_generales_repo
from rules_repository import RepositorioReglas
import sys
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "backend", "app", "core")))
from formula_engine import validar_formula
from rule_engine import normalizar_texto, BANK_STOPWORDS
import orange_intelligence

def _int_seguro(valor, default=100) -> int:
    try:
        return int(str(valor).strip())
    except (ValueError, TypeError):
        return default

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

reglas_bp = Blueprint('reglas', __name__)

# --- Reglas ---

@reglas_bp.route("/empresas/<int:empresa_id>/reglas")
@login_required
@empresa_requerida
def reglas_lista(empresa_id):
    repo = RepositorioReglas()
    reglas_completas = repo.listar_reglas_completas(empresa_id)
    return render_template("reglas.html", reglas=reglas_completas)

@reglas_bp.route("/empresas/<int:empresa_id>/reglas/nueva", methods=["GET", "POST"])
@login_required
@empresa_requerida
def regla_nueva(empresa_id):
    if request.method == "POST":
        repo = RepositorioReglas()

        cuentas = request.form.getlist("linea_cuenta")
        naturalezas = request.form.getlist("linea_naturaleza")
        formulas = request.form.getlist("linea_formula")
        descripciones = request.form.getlist("linea_descripcion")

        plantilla = [
            {"cuenta": c, "naturaleza": n, "formula": f, "descripcion_linea": d or None}
            for c, n, f, d in zip_longest(cuentas, naturalezas, formulas, descripciones, fillvalue="")
            if c and n and f
        ]

        formulas_invalidas = [
            f"Línea {idx+1} ({item['cuenta']}): '{item['formula']}'"
            for idx, item in enumerate(plantilla)
            if not validar_formula(item.get("formula", ""))
        ]
        if formulas_invalidas:
            flash(f"Fórmula contable inválida en: {'; '.join(formulas_invalidas)}. Usa variables válidas como TOTAL, BASE, IVA.", "error")
            cuentas = catalogo_repo.listar(empresa_id)
            return render_template("regla_form.html", cuentas=cuentas), 400

        palabras = [p.strip() for p in request.form.get("palabras_clave", "").split(",") if p.strip()]

        repo.crear_regla(
            empresa_id=empresa_id,
            nombre=request.form["nombre"],
            prioridad=_int_seguro(request.form.get("prioridad"), 100),
            tipo_movimiento=request.form.get("tipo_movimiento") or None,
            rfc_contraparte=request.form.get("rfc_contraparte") or None,
            cuenta_bancaria_contraparte=request.form.get("cuenta_bancaria_contraparte") or None,
            descripcion_exacta=request.form.get("descripcion_exacta") or None,
            palabras_clave=palabras,
            plantilla=plantilla,
            creada_por=g.usuario["id"],
        )
        flash("Regla creada.", "exito")
        return redirect(url_for("reglas.reglas_lista", empresa_id=empresa_id))

    cuentas = catalogo_repo.listar(empresa_id)
    return render_template("regla_form.html", cuentas=cuentas)

@reglas_bp.route("/empresas/<int:empresa_id>/reglas/<int:regla_id>/eliminar", methods=["POST"])
@login_required
@empresa_requerida
def regla_eliminar(empresa_id, regla_id):
    eliminada = RepositorioReglas().eliminar_regla(regla_id, empresa_id=empresa_id)
    if not eliminada:
        flash("Regla no encontrada en esta empresa.", "error")
        return redirect(url_for("reglas.reglas_lista", empresa_id=empresa_id)), 404
    flash("Regla eliminada.", "exito")
    return redirect(url_for("reglas.reglas_lista", empresa_id=empresa_id))

@reglas_bp.route("/empresas/<int:empresa_id>/reglas/<int:regla_id>/toggle", methods=["POST"])
@login_required
@empresa_requerida
def regla_toggle(empresa_id, regla_id):
    repo = RepositorioReglas()
    reglas = repo.listar_reglas(empresa_id)
    regla = next((r for r in reglas if r["id"] == regla_id), None)
    if not regla:
        flash("Regla no encontrada en esta empresa.", "error")
        return redirect(url_for("reglas.reglas_lista", empresa_id=empresa_id)), 404
    if regla["activa"]:
        repo.desactivar_regla(regla_id, empresa_id=empresa_id)
    else:
        repo.activar_regla(regla_id, empresa_id=empresa_id)
    return redirect(url_for("reglas.reglas_lista", empresa_id=empresa_id))

# --- Pendientes de revisión (aprendizaje) ---

@reglas_bp.route("/empresas/<int:empresa_id>/pendientes")
@login_required
@empresa_requerida
def pendientes_siguiente(empresa_id):
    """Asistente guiado: muestra UN movimiento pendiente a la vez, con
    búsqueda de cuenta contra el catálogo y marca de IVA, igual que
    tuy.py. Al guardar, crea la regla automáticamente y pasa al
    siguiente. Es el camino principal; el modo lista completa
    (con fórmulas manuales) sigue disponible en /pendientes/avanzado."""
    omitidos = [int(x) for x in session.get("pendientes_omitidos", []) if str(x).isdigit()]
    mov_especifico_id = request.args.get("movimiento_id")
    with get_connection() as con:
        if mov_especifico_id and str(mov_especifico_id).isdigit():
            movimiento = con.execute(
                "SELECT * FROM movimientos WHERE id = ? AND empresa_id = ?",
                (int(mov_especifico_id), empresa_id)
            ).fetchone()
        else:
            movimiento = con.execute(
                f"""SELECT * FROM movimientos WHERE empresa_id = ? AND estado_clasificacion IN ('pendiente', 'sugerido')
                    {"AND id NOT IN (" + ",".join("?" * len(omitidos)) + ")" if omitidos else ""}
                    ORDER BY id ASC LIMIT 1""",
                (empresa_id, *omitidos),
            ).fetchone()
        total_pendientes = con.execute(
            "SELECT COUNT(*) FROM movimientos WHERE empresa_id = ? AND estado_clasificacion IN ('pendiente', 'sugerido')",
            (empresa_id,),
        ).fetchone()[0]

    if movimiento is None:
        session.pop("pendientes_omitidos", None)
        return render_template("pendientes_siguiente.html", movimiento=None, total_pendientes=total_pendientes)

    _, sugerencia = movimientos_repo.sugerencia_para_movimiento(movimiento["id"], empresa_id=empresa_id)
    cuentas = catalogo_repo.listar(empresa_id)
    empresa = bancos_repo.obtener_empresa(empresa_id)

    with get_connection() as con:
        banco = con.execute(
            """SELECT b.* FROM bancos b JOIN documentos_importados d ON d.banco_id = b.id
               WHERE d.id = ? AND d.empresa_id = ?""",
            (movimiento["documento_id"], empresa_id),
        ).fetchone()

        if not banco:
            # Fallback automático al primer banco configurado con cuenta contable de la empresa
            banco = con.execute(
                "SELECT * FROM bancos WHERE empresa_id = ? AND cuenta_contable IS NOT NULL AND cuenta_contable != '' ORDER BY id ASC LIMIT 1",
                (empresa_id,),
            ).fetchone()

        regla_sugerida = None
        if movimiento["regla_id"]:
            regla_sugerida = con.execute(
                "SELECT id, nombre FROM reglas WHERE id = ?", (movimiento["regla_id"],)
            ).fetchone()

        # Si ya hay uno o varios CFDIs confirmados para este movimiento, el sistema ya
        # conoce el RFC y los impuestos exactos: no hace falta preguntar
        # IVA ni afectable, solo la cuenta contable del cliente/proveedor.
        cfdis_raw = con.execute(
            """SELECT c.id, c.uuid, c.serie, c.folio, c.fecha, c.rfc_emisor, c.nombre_emisor, c.rfc_receptor, c.nombre_receptor, c.total,
                      cm.importe_aplicado, cm.motivo
               FROM cfdi_movimiento cm
               JOIN cfdis c ON c.id = cm.cfdi_id
               WHERE cm.movimiento_id = ? AND cm.confirmado = 1
               ORDER BY c.id ASC""",
            (movimiento["id"],),
        ).fetchall()

        # Deduplicar estrictamente por UUID o ID para garantizar que jamás aparezca un CFDI duplicado
        cfdis_vistos = set()
        cfdis_asociados = []
        for c in cfdis_raw:
            key_u = (c["uuid"] or "").strip().upper() or str(c["id"])
            if key_u not in cfdis_vistos:
                cfdis_vistos.add(key_u)
                cfdis_asociados.append(c)

        cfdi_info = None
        if cfdis_asociados:
            cfdi_ids = [c["id"] for c in cfdis_asociados]
            placeholders = ",".join(["?"] * len(cfdi_ids))
            imp_res = con.execute(
                f"""SELECT SUM(CASE WHEN tipo_impuesto = 'IVA' AND es_retencion = 0 THEN base ELSE 0 END) AS base_total,
                           SUM(CASE WHEN tipo_impuesto = 'IVA' AND es_retencion = 0 THEN importe ELSE 0 END) AS iva_total
                    FROM cfdi_impuestos
                    WHERE cfdi_id IN ({placeholders})""",
                tuple(cfdi_ids),
            ).fetchone()

            base_tot = float(imp_res["base_total"] or 0.0) if imp_res else 0.0
            iva_tot = float(imp_res["iva_total"] or 0.0) if imp_res else 0.0
            tasa_calc = round(iva_tot / base_tot, 4) if base_tot > 0 else 0.16

            primer_cfdi = cfdis_asociados[0]
            cfdi_info = {
                "id": primer_cfdi["id"],
                "uuid": primer_cfdi["uuid"],
                "serie": primer_cfdi["serie"],
                "folio": primer_cfdi["folio"],
                "rfc_emisor": primer_cfdi["rfc_emisor"],
                "nombre_emisor": primer_cfdi["nombre_emisor"],
                "rfc_receptor": primer_cfdi["rfc_receptor"],
                "nombre_receptor": primer_cfdi["nombre_receptor"],
                "total": primer_cfdi["total"],
                "base": base_tot,
                "iva_importe": iva_tot,
                "tasa": tasa_calc,
            }

    poliza_anticipo = None
    poliza_aplicacion = None
    if movimiento:
        with get_connection() as con_p:
            if movimiento.get("poliza_id"):
                poliza_anticipo = con_p.execute("SELECT * FROM polizas WHERE id = ?", (movimiento["poliza_id"],)).fetchone()
            if movimiento.get("poliza_aplicacion_id"):
                poliza_aplicacion = con_p.execute("SELECT * FROM polizas WHERE id = ?", (movimiento["poliza_aplicacion_id"],)).fetchone()

    return render_template(
        "pendientes_siguiente.html", movimiento=movimiento, sugerencia=sugerencia,
        cuentas=cuentas, empresa=empresa, banco=banco, cfdi_info=cfdi_info,
        cfdis_asociados=cfdis_asociados,
        poliza_anticipo=poliza_anticipo,
        poliza_aplicacion=poliza_aplicacion,
        regla_sugerida=regla_sugerida,
        total_pendientes=total_pendientes,
        restantes=total_pendientes - len(omitidos),
    )

@reglas_bp.route("/empresas/<int:empresa_id>/pendientes/<int:movimiento_id>/confirmar-sugerencia", methods=["POST"])
@login_required
@empresa_requerida
def pendiente_confirmar_sugerencia(empresa_id, movimiento_id):
    with get_connection() as con:
        mov = con.execute(
            "SELECT * FROM movimientos WHERE id = ? AND empresa_id = ?",
            (movimiento_id, empresa_id),
        ).fetchone()
        if not mov or not mov["regla_id"]:
            flash("Movimiento o regla sugerida no encontrada.", "error")
            return redirect(url_for("reglas.pendientes_siguiente", empresa_id=empresa_id))

        con.execute(
            "UPDATE movimientos SET estado_clasificacion = 'manual' WHERE id = ? AND empresa_id = ?",
            (movimiento_id, empresa_id),
        )
        con.execute(
            "UPDATE reglas SET veces_aplicada = veces_aplicada + 1 WHERE id = ?",
            (mov["regla_id"],),
        )
    flash("Sugerencia autorizada exitosamente.", "exito")
    return redirect(url_for("reglas.pendientes_siguiente", empresa_id=empresa_id))

@reglas_bp.route("/empresas/<int:empresa_id>/pendientes/<int:movimiento_id>/omitir", methods=["POST"])
@login_required
@empresa_requerida
def pendiente_omitir(empresa_id, movimiento_id):
    with get_connection() as con:
        existe = con.execute(
            "SELECT id FROM movimientos WHERE id = ? AND empresa_id = ?",
            (movimiento_id, empresa_id),
        ).fetchone()
    if not existe:
        return redirect(url_for("reglas.pendientes_siguiente", empresa_id=empresa_id)), 404
    omitidos = session.get("pendientes_omitidos", [])
    omitidos.append(movimiento_id)
    session["pendientes_omitidos"] = omitidos
    return redirect(url_for("reglas.pendientes_siguiente", empresa_id=empresa_id))

@reglas_bp.route("/empresas/<int:empresa_id>/pendientes/<int:movimiento_id>/asignar-rapido", methods=["POST"])
@login_required
@empresa_requerida
def pendiente_asignar_rapido(empresa_id, movimiento_id):
    with get_connection() as con:
        movimiento = con.execute(
            "SELECT * FROM movimientos WHERE id = ? AND empresa_id = ?",
            (movimiento_id, empresa_id),
        ).fetchone()
        if movimiento is None:
            flash("Movimiento no encontrado en esta empresa.", "error")
            return redirect(url_for("reglas.pendientes_siguiente", empresa_id=empresa_id)), 404

        tiene_cfdi = con.execute(
            "SELECT 1 FROM cfdi_movimiento WHERE movimiento_id = ? AND confirmado = 1",
            (movimiento_id,),
        ).fetchone() is not None

    cuenta_contraparte = request.form["cuenta_contraparte"].strip()
    cuenta_banco = request.form.get("cuenta_banco", "").strip()
    if not cuenta_banco:
        with get_connection() as con_b:
            b_row = con_b.execute(
                """SELECT b.cuenta_contable FROM bancos b JOIN documentos_importados d ON d.banco_id = b.id
                   WHERE d.id = ? AND d.empresa_id = ?""",
                (movimiento["documento_id"], empresa_id),
            ).fetchone()
            if not b_row or not b_row["cuenta_contable"]:
                b_row = con_b.execute(
                    "SELECT cuenta_contable FROM bancos WHERE empresa_id = ? AND cuenta_contable IS NOT NULL AND cuenta_contable != '' ORDER BY id ASC LIMIT 1",
                    (empresa_id,),
                ).fetchone()
            if b_row and b_row["cuenta_contable"]:
                cuenta_banco = b_row["cuenta_contable"]
    aplicar_a = request.form.get("aplicar_a", "solo_similares")  # 'solo_este' | 'solo_similares'

    if tiene_cfdi:
        # El CFDI ya confirma que es fiscal y que tiene impuestos; no se
        # le pregunta al usuario, se asume.
        tiene_iva = True
        afectable_impuestos = True
    else:
        tiene_iva = request.form.get("tiene_iva") == "on"
        afectable_impuestos = request.form.get("afectable_impuestos") == "on"

    empresa = bancos_repo.obtener_empresa(empresa_id)
    error_iva = validar_cuentas_iva_configuradas(
        movimiento["tipo"], tiene_iva, afectable_impuestos, empresa, cuenta_contraparte=cuenta_contraparte
    )
    if error_iva:
        flash(error_iva, "error")
        return redirect(url_for("reglas.pendientes_siguiente", empresa_id=empresa_id))

    lineas_generales = reglas_generales_repo.listar(empresa_id, movimiento["tipo"]) if afectable_impuestos else []
    plantilla = construir_plantilla_simple(
        movimiento["tipo"], tiene_iva, afectable_impuestos, cuenta_contraparte,
        cuenta_banco, empresa, lineas_generales=lineas_generales,
    )

    mov_dict, sugerencia = movimientos_repo.sugerencia_para_movimiento(movimiento_id, empresa_id=empresa_id)

    if aplicar_a == "solo_este":
        sugerencia.tipo_coincidencia = "exacta"
        sugerencia.valor_coincidencia = mov_dict["descripcion"]
        sugerencia.nombre_sugerido = f"Movimiento: {mov_dict['descripcion'][:40]}"

    repo = RepositorioReglas()
    kwargs = {
        "empresa_id": empresa_id, "nombre": sugerencia.nombre_sugerido,
        "tipo_movimiento": movimiento["tipo"], "plantilla": plantilla,
        "creada_por": g.usuario["id"],
    }
    if sugerencia.tipo_coincidencia == "rfc":
        kwargs["rfc_contraparte"] = sugerencia.valor_coincidencia
        if getattr(sugerencia, "palabra_clave_fallback", ""):
            kwargs["palabras_clave"] = [sugerencia.palabra_clave_fallback]
        elif getattr(sugerencia, "descripcion_exacta_fallback", ""):
            kwargs["descripcion_exacta"] = sugerencia.descripcion_exacta_fallback
    elif sugerencia.tipo_coincidencia == "cuenta_bancaria":
        kwargs["cuenta_bancaria_contraparte"] = sugerencia.valor_coincidencia
        if getattr(sugerencia, "palabra_clave_fallback", ""):
            kwargs["palabras_clave"] = [sugerencia.palabra_clave_fallback]
        elif getattr(sugerencia, "descripcion_exacta_fallback", ""):
            kwargs["descripcion_exacta"] = sugerencia.descripcion_exacta_fallback
    elif sugerencia.tipo_coincidencia == "exacta":
        kwargs["descripcion_exacta"] = sugerencia.valor_coincidencia
    else:
        kwargs["palabras_clave"] = [sugerencia.valor_coincidencia]

    regla_id = repo.crear_regla(**kwargs)
    with get_connection() as con:
        con.execute(
            "UPDATE movimientos SET regla_id = ?, estado_clasificacion = 'manual' WHERE id = ? AND empresa_id = ?",
            (regla_id, movimiento_id, empresa_id),
        )
    try:
        from asociacion_repo import asociar_automaticamente
        asociar_automaticamente(empresa_id)
    except Exception:
        pass
    resultado = movimientos_repo.clasificar_pendientes(empresa_id)

    flash(
        f"Regla creada. {resultado['automaticos']} movimiento(s) clasificado(s) con ella.",
        "exito",
    )
    descuadres = resultado.get("descuadres") or []
    if descuadres:
        ejemplos = " | ".join(d["mensaje"] for d in descuadres[:3])
        extra = f" (+{len(descuadres) - 3} más)" if len(descuadres) > 3 else ""
        flash(
            f"Atención: {len(descuadres)} movimiento(s) coincidieron con una regla pero no cuadraron: {ejemplos}{extra}",
            "error",
        )
    return redirect(url_for("reglas.pendientes_siguiente", empresa_id=empresa_id))

@reglas_bp.route("/empresas/<int:empresa_id>/pendientes/avanzado")
@login_required
@empresa_requerida
def pendientes(empresa_id):
    """Modo avanzado: lista todos los pendientes a la vez con sus sugerencias
    en una sola conexión y consulta, eliminando el patrón N+1."""
    with get_connection() as con:
        empresa = con.execute("SELECT rfc FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
        rfc_empresa = empresa["rfc"] if empresa else ""
        filas = con.execute(
            "SELECT * FROM movimientos WHERE empresa_id = ? AND estado_clasificacion IN ('pendiente', 'sugerido') ORDER BY fecha ASC",
            (empresa_id,),
        ).fetchall()
        
    items = []
    from learning_engine import sugerir_regla_desde_clasificacion_manual
    for f in filas:
        mov = movimientos_repo._fila_a_movimiento_dict(f)
        sug = sugerir_regla_desde_clasificacion_manual(mov, rfc_empresa=rfc_empresa)
        items.append({"movimiento": mov, "sugerencia": sug})
        
    cuentas = catalogo_repo.listar(empresa_id)
    return render_template("pendientes.html", items=items, cuentas=cuentas)

@reglas_bp.route("/empresas/<int:empresa_id>/pendientes/<int:movimiento_id>/crear-regla", methods=["POST"])
@login_required
@empresa_requerida
def pendiente_crear_regla(empresa_id, movimiento_id):
    with get_connection() as con:
        existe = con.execute(
            "SELECT id FROM movimientos WHERE id = ? AND empresa_id = ?",
            (movimiento_id, empresa_id),
        ).fetchone()
    if not existe:
        flash("Movimiento no encontrado en esta empresa.", "error")
        return redirect(url_for("reglas.pendientes", empresa_id=empresa_id)), 404

    tipo_coincidencia = request.form["tipo_coincidencia"]
    valor_coincidencia = request.form["valor_coincidencia"].strip()
    nombre_regla = request.form["nombre_regla"].strip()
    tipo_movimiento = request.form.get("tipo_movimiento") or None

    cuentas = request.form.getlist("linea_cuenta")
    naturalezas = request.form.getlist("linea_naturaleza")
    formulas = request.form.getlist("linea_formula")
    descripciones = request.form.getlist("linea_descripcion")
    plantilla = [
        {"cuenta": c, "naturaleza": n, "formula": f, "descripcion_linea": d or None}
        for c, n, f, d in zip_longest(cuentas, naturalezas, formulas, descripciones, fillvalue="")
        if c and n and f
    ]

    formulas_invalidas = [
        f"Línea {idx+1} ({item['cuenta']}): '{item['formula']}'"
        for idx, item in enumerate(plantilla)
        if not validar_formula(item.get("formula", ""))
    ]
    if formulas_invalidas:
        flash(f"No se puede crear la regla: fórmula contable inválida en {'; '.join(formulas_invalidas)}. Usa variables como TOTAL, BASE, IVA.", "error")
        return redirect(url_for("reglas.pendientes", empresa_id=empresa_id)), 400

    repo = RepositorioReglas()
    kwargs = {
        "empresa_id": empresa_id, "nombre": nombre_regla, "tipo_movimiento": tipo_movimiento,
        "plantilla": plantilla, "creada_por": g.usuario["id"],
    }
    if tipo_coincidencia == "rfc":
        kwargs["rfc_contraparte"] = valor_coincidencia
    elif tipo_coincidencia == "cuenta_bancaria":
        kwargs["cuenta_bancaria_contraparte"] = valor_coincidencia
    elif tipo_coincidencia == "exacta":
        kwargs["descripcion_exacta"] = valor_coincidencia
    else:
        norm_val = normalizar_texto(valor_coincidencia)
        if norm_val in BANK_STOPWORDS or len(norm_val) < 4:
            flash(
                f"La palabra clave '{valor_coincidencia}' es un término bancario genérico o demasiado corto. "
                f"Usa un nombre específico del proveedor, su RFC o coincidencia exacta para no desviar otros movimientos contables.",
                "error",
            )
            return redirect(url_for("reglas.pendientes", empresa_id=empresa_id)), 400
        kwargs["palabras_clave"] = [valor_coincidencia]

    repo.crear_regla(**kwargs)
    try:
        from asociacion_repo import asociar_automaticamente
        asociar_automaticamente(empresa_id)
    except Exception:
        pass
    resultado = movimientos_repo.clasificar_pendientes(empresa_id)

    flash("Regla creada y aplicada a los movimientos pendientes.", "exito")
    descuadres = resultado.get("descuadres") or []
    if descuadres:
        ejemplos = " | ".join(d["mensaje"] for d in descuadres[:3])
        extra = f" (+{len(descuadres) - 3} más)" if len(descuadres) > 3 else ""
        flash(
            f"Atención: {len(descuadres)} movimiento(s) coincidieron con una regla pero no cuadraron: {ejemplos}{extra}",
            "error",
        )
    return redirect(url_for("reglas.pendientes", empresa_id=empresa_id))

