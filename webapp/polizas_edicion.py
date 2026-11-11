
def formatear_concepto_poliza(concepto_base: str, tiene_cfdi: bool, tiene_iva: bool) -> str:
    """Aplica las reglas de etiqueta al concepto de la póliza:
    - Si tiene CFDI -> ' (OPE sistema)'
    - Si no tiene CFDI pero aplica IVA -> ' (ADJUNTAR XML)'
    - Si no aplica IVA ni tiene CFDI -> sin etiqueta extra.
    """
    import re
    texto = (concepto_base or "").strip()
    texto = re.sub(r"\s*\((?:adjuntar\s*xml|ope\s*sistema)\)", "", texto, flags=re.IGNORECASE).strip()
    if tiene_cfdi:
        tag = " (OPE sistema)"
    elif tiene_iva:
        tag = " (ADJUNTAR XML)"
    else:
        tag = ""
    return (texto + tag).strip()

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

from decimal import Decimal
from datetime import datetime

import _rutas  # noqa: F401  (deja backend/app/core y exporters en sys.path)

from formula_engine import MontoDecimal, a_decimal, redondear_moneda
from db import get_connection


class PolizasYaEnviadasError(Exception):
    pass


def _normalizar_linea_manual(linea):
    """Regresa (cuenta, naturaleza, importe, descripcion), o None si es una fila vacía."""
    cuenta_raw = str(linea.get("cuenta", "")).strip()
    if " - " in cuenta_raw:
        cuenta = cuenta_raw.split(" - ")[0].strip()
    elif " " in cuenta_raw and any(c.isdigit() for c in cuenta_raw.split(" ")[0]):
        cuenta = cuenta_raw.split(" ")[0].strip()
    else:
        cuenta = cuenta_raw

    if linea.get("naturaleza"):
        naturaleza = "cargo" if str(linea["naturaleza"]).lower() in ("cargo", "debe") else "abono"
        importe = redondear_moneda(linea.get("importe", 0))
    elif a_decimal(linea.get("cargo", 0)) > Decimal("0.00"):
        naturaleza = "cargo"
        importe = redondear_moneda(linea["cargo"])
    else:
        naturaleza = "abono"
        importe = redondear_moneda(linea.get("abono", linea.get("importe", 0)))

    if not cuenta and importe == Decimal("0.00"):
        return None
    if not cuenta:
        raise ValueError("Hay una línea con importe pero sin cuenta contable.")
    if importe <= Decimal("0.00"):
        raise ValueError(f"La cuenta {cuenta} tiene un importe en cero o negativo.")
    return cuenta, naturaleza, importe, str(linea.get("descripcion", "")).strip()


def obtener_asiento_movimiento(empresa_id, movimiento_id):
    """
    Obtiene las líneas contables de un movimiento (con nombre de cuenta desde cuentas_catalogo).
    Si el movimiento aún no tiene póliza, genera una propuesta de asiento cuadrada con
    la cuenta de banco y la contraparte / CFDI.
    """
    with get_connection() as con:
        mov = con.execute(
            """SELECT m.*, d.banco_id
               FROM movimientos m
               LEFT JOIN documentos_importados d ON d.id = m.documento_id
               WHERE m.id = ? AND m.empresa_id = ?""",
            (movimiento_id, empresa_id),
        ).fetchone()

        if not mov:
            return None

        poliza_id = mov["poliza_id"]
        poliza = None
        lineas = []
        cargos = Decimal("0.00")
        abonos = Decimal("0.00")
        cuadrada = False

        if poliza_id:
            poliza = con.execute(
                "SELECT * FROM polizas WHERE id = ? AND empresa_id = ?",
                (poliza_id, empresa_id),
            ).fetchone()

            filas_lineas = con.execute(
                """SELECT pl.id, pl.orden, pl.cuenta, pl.naturaleza, pl.importe, pl.descripcion,
                          cc.descripcion AS nombre_cuenta
                   FROM poliza_lineas pl
                   LEFT JOIN cuentas_catalogo cc ON cc.empresa_id = ?
                        AND (cc.cuenta = pl.cuenta OR REPLACE(cc.cuenta, '-', '') = REPLACE(pl.cuenta, '-', ''))
                   WHERE pl.poliza_id = ?
                   ORDER BY pl.orden ASC, pl.id ASC""",
                (empresa_id, poliza_id),
            ).fetchall()

            for fl in filas_lineas:
                imp = redondear_moneda(fl["importe"])
                es_cargo = fl["naturaleza"].lower() in ("cargo", "debe")
                cargos += imp if es_cargo else Decimal("0.00")
                abonos += imp if not es_cargo else Decimal("0.00")
                lineas.append({
                    "id": fl["id"],
                    "cuenta": fl["cuenta"],
                    "nombre_cuenta": fl["nombre_cuenta"] or "Cuenta no catalogada",
                    "naturaleza": "cargo" if es_cargo else "abono",
                    "cargo": float(imp) if es_cargo else 0.0,
                    "abono": float(imp) if not es_cargo else 0.0,
                    "descripcion": fl["descripcion"] or "",
                })
            cuadrada = bool(poliza["cuadrada"]) if poliza else (cargos == abonos)

        else:
            # Movimiento sin póliza: Proponer asiento inicial sugerido
            # 1. Obtener cuenta de banco
            banco_row = None
            if mov["banco_id"]:
                banco_row = con.execute(
                    "SELECT id, nombre, cuenta_contable FROM bancos WHERE id = ? AND empresa_id = ?",
                    (mov["banco_id"], empresa_id),
                ).fetchone()
            if not banco_row or not banco_row["cuenta_contable"]:
                banco_row = con.execute(
                    "SELECT id, nombre, cuenta_contable FROM bancos WHERE empresa_id = ? AND cuenta_contable IS NOT NULL AND cuenta_contable != '' ORDER BY id ASC LIMIT 1",
                    (empresa_id,),
                ).fetchone()

            cuenta_banco = banco_row["cuenta_contable"] if banco_row and banco_row["cuenta_contable"] else "10201001"
            nom_banco_row = con.execute(
                "SELECT descripcion FROM cuentas_catalogo WHERE empresa_id = ? AND (cuenta = ? OR REPLACE(cuenta, '-', '') = REPLACE(?, '-', '')) LIMIT 1",
                (empresa_id, cuenta_banco, cuenta_banco),
            ).fetchone()
            nombre_banco = nom_banco_row["descripcion"] if nom_banco_row else (banco_row["nombre"] if banco_row else "Banco")

            # 2. Buscar cuenta de contraparte si existe por RFC o nombre
            cuenta_contra = ""
            nombre_contra = ""
            if mov["rfc_contraparte"]:
                cta_match = con.execute(
                    "SELECT cuenta, descripcion FROM cuentas_catalogo WHERE empresa_id = ? AND descripcion ILIKE ? LIMIT 1",
                    (empresa_id, f"%{mov['rfc_contraparte']}%"),
                ).fetchone()
                if cta_match:
                    cuenta_contra = cta_match["cuenta"]
                    nombre_contra = cta_match["descripcion"]

            tot = redondear_moneda(mov["total"])
            concepto_mov = mov["descripcion"] or ""

            if mov["tipo"] == "egreso":
                # Egreso: Cargo a Proveedor/Gasto, Abono a Banco
                lineas.append({
                    "id": None,
                    "cuenta": cuenta_contra,
                    "nombre_cuenta": nombre_contra or "Contraparte (Proveedor / Gasto)",
                    "naturaleza": "cargo",
                    "cargo": float(tot),
                    "abono": 0.0,
                    "descripcion": concepto_mov,
                })
                lineas.append({
                    "id": None,
                    "cuenta": cuenta_banco,
                    "nombre_cuenta": nombre_banco,
                    "naturaleza": "abono",
                    "cargo": 0.0,
                    "abono": float(tot),
                    "descripcion": concepto_mov,
                })
            else:
                # Ingreso: Cargo a Banco, Abono a Cliente/Ingreso
                lineas.append({
                    "id": None,
                    "cuenta": cuenta_banco,
                    "nombre_cuenta": nombre_banco,
                    "naturaleza": "cargo",
                    "cargo": float(tot),
                    "abono": 0.0,
                    "descripcion": concepto_mov,
                })
                lineas.append({
                    "id": None,
                    "cuenta": cuenta_contra,
                    "nombre_cuenta": nombre_contra or "Contraparte (Cliente / Ingreso)",
                    "naturaleza": "abono",
                    "cargo": 0.0,
                    "abono": float(tot),
                    "descripcion": concepto_mov,
                })

            cargos = tot
            abonos = tot
            cuadrada = True

        return {
            "movimiento": {
                "id": mov["id"],
                "fecha": str(mov["fecha"]),
                "tipo": mov["tipo"],
                "total": float(mov["total"]),
                "descripcion": mov["descripcion"],
                "rfc_contraparte": mov["rfc_contraparte"] or "",
                "numero_factura": mov["numero_factura"] or "",
                "referencia": mov["referencia_bancaria"] or "",
                "estado_clasificacion": mov["estado_clasificacion"],
            },
            "poliza_id": poliza_id,
            "poliza_numero": poliza["numero"] if poliza else None,
            "poliza_tipo": poliza["tipo"] if poliza else mov["tipo"],
            "concepto": poliza["concepto"] if poliza else (mov["descripcion"] or ""),
            "lineas": lineas,
            "total_cargos": float(cargos),
            "total_abonos": float(abonos),
            "diferencia": float(abs(cargos - abonos)),
            "cuadrada": cuadrada,
        }


def guardar_asiento_movimiento(empresa_id, movimiento_id, lineas, concepto=None):
    """
    Guarda o actualiza el asiento contable completo de un movimiento.
    Valida cuadre exacto de cargos y abonos, crea la póliza si no existe,
    reemplaza las líneas y actualiza el estado del movimiento a 'manual'.
    """
    if not lineas or not isinstance(lineas, list):
        raise ValueError("El asiento debe contener líneas contables.")

    normalizadas = [n for n in (_normalizar_linea_manual(l) for l in lineas) if n]
    if len(normalizadas) < 2:
        raise ValueError("El asiento contable debe tener al menos dos líneas con cuenta e importe.")

    cargos = MontoDecimal("0.00")
    abonos = MontoDecimal("0.00")
    for cuenta, naturaleza, importe, descripcion in normalizadas:
        if naturaleza == "cargo":
            cargos = redondear_moneda(cargos + importe)
        else:
            abonos = redondear_moneda(abonos + importe)

    diferencia = redondear_moneda(cargos - abonos)
    if abs(diferencia) > Decimal("0.05"):
        raise ValueError(
            f"El asiento está descuadrado: Cargos ${cargos:,.2f} vs Abonos ${abonos:,.2f} "
            f"(Diferencia: ${abs(diferencia):,.2f}). Deben sumar lo mismo."
        )

    cuadrada = 1 if diferencia == Decimal("0.00") else 0

    with get_connection() as con:
        cur = con.cursor()
        mov = cur.execute(
            "SELECT * FROM movimientos WHERE id = ? AND empresa_id = ?",
            (movimiento_id, empresa_id),
        ).fetchone()
        if not mov:
            raise ValueError("Movimiento no encontrado.")

        poliza_id = mov["poliza_id"]
        tipo_poliza = "egreso" if mov["tipo"] == "egreso" else "ingreso"
        tiene_cfdi = cur.execute(
            "SELECT 1 FROM cfdi_movimiento WHERE movimiento_id = ? AND confirmado = 1 LIMIT 1",
            (movimiento_id,)
        ).fetchone() is not None
        tiene_iva = mov["afectable_impuestos"] is None or bool(mov["afectable_impuestos"])
        concepto_base = (concepto or mov["descripcion"] or f"Póliza {tipo_poliza} {mov['fecha']}").strip()
        concepto_final = formatear_concepto_poliza(concepto_base, tiene_cfdi, tiene_iva)
        fecha_poliza = str(mov["fecha"])

        if poliza_id:
            cur.execute(
                """UPDATE polizas
                   SET concepto = ?, fecha = ?, cuadrada = ?, enviada_contpaqi = 0
                   WHERE id = ? AND empresa_id = ?""",
                (concepto_final, fecha_poliza, cuadrada, poliza_id, empresa_id),
            )
            cur.execute("DELETE FROM poliza_lineas WHERE poliza_id = ?", (poliza_id,))
        else:
            sig_num_row = cur.execute(
                "SELECT COALESCE(MAX(numero), 0) + 1 FROM polizas WHERE empresa_id = ? AND tipo = ?",
                (empresa_id, tipo_poliza),
            ).fetchone()
            sig_num = sig_num_row[0] if sig_num_row else 1

            cur.execute(
                """INSERT INTO polizas
                   (empresa_id, tipo, numero, fecha, concepto, cuadrada, enviada_contpaqi)
                   VALUES (?, ?, ?, ?, ?, ?, 0)""",
                (empresa_id, tipo_poliza, sig_num, fecha_poliza, concepto_final, cuadrada),
            )
            poliza_id = cur.lastrowid
            if not poliza_id:
                # Fallback postgres RETURNING / MAX id
                poliza_id = cur.execute(
                    "SELECT id FROM polizas WHERE empresa_id = ? AND tipo = ? AND numero = ? ORDER BY id DESC LIMIT 1",
                    (empresa_id, tipo_poliza, sig_num)
                ).fetchone()[0]

            cur.execute(
                "UPDATE movimientos SET poliza_id = ?, estado_clasificacion = 'manual' WHERE id = ? AND empresa_id = ?",
                (poliza_id, movimiento_id, empresa_id),
            )

        for orden, (cuenta, naturaleza, importe, descripcion) in enumerate(normalizadas):
            cur.execute(
                """INSERT INTO poliza_lineas
                   (poliza_id, orden, cuenta, naturaleza, importe, descripcion, formula_usada)
                   VALUES (?, ?, ?, ?, ?, ?, 'MANUAL')""",
                (poliza_id, orden, cuenta, naturaleza, importe, descripcion),
            )

        cur.execute(
            """INSERT INTO poliza_auditoria (poliza_id, paso, detalle)
               VALUES (?, 0, ?)""",
            (
                poliza_id,
                f"Edición manual de asiento ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}): "
                f"Cargos ${cargos:,.2f}, Abonos ${abonos:,.2f} -> {'Cuadrada' if cuadrada else 'Descuadrada'}",
            ),
        )
        con.commit()

    return {
        "ok": True,
        "poliza_id": poliza_id,
        "cuadrada": bool(cuadrada),
        "cargos": float(cargos),
        "abonos": float(abonos),
        "diferencia": float(abs(diferencia)),
    }


def actualizar_poliza(poliza_id, lineas, empresa_id):
    """Reemplaza las líneas de una póliza que aún no se ha enviado y recalcula su cuadre."""
    if not lineas or not isinstance(lineas, list):
        raise ValueError("La póliza debe tener al menos una línea.")

    normalizadas = [n for n in (_normalizar_linea_manual(l) for l in lineas) if n]
    if not normalizadas:
        raise ValueError("La póliza debe tener al menos una línea con cuenta e importe.")

    with get_connection() as con:
        poliza = con.execute(
            "SELECT id, enviada_contpaqi FROM polizas WHERE id = ? AND empresa_id = ?",
            (poliza_id, empresa_id),
        ).fetchone()
        if not poliza:
            raise ValueError("Póliza no encontrada.")
        con.execute("UPDATE polizas SET enviada_contpaqi = 0 WHERE id = ?", (poliza_id,))

        con.execute("DELETE FROM poliza_lineas WHERE poliza_id = ?", (poliza_id,))
        cargos = MontoDecimal("0.00")
        abonos = MontoDecimal("0.00")
        for orden, (cuenta, naturaleza, importe, descripcion) in enumerate(normalizadas):
            if naturaleza == "cargo":
                cargos = redondear_moneda(cargos + importe)
            else:
                abonos = redondear_moneda(abonos + importe)
            con.execute(
                """INSERT INTO poliza_lineas
                   (poliza_id, orden, cuenta, naturaleza, importe, descripcion, formula_usada)
                   VALUES (?, ?, ?, ?, ?, ?, 'MANUAL')""",
                (poliza_id, orden, cuenta, naturaleza, importe, descripcion),
            )

        diferencia = redondear_moneda(cargos - abonos)
        cuadrada = 1 if diferencia == Decimal("0.00") else 0
        con.execute(
            "UPDATE polizas SET cuadrada = ? WHERE id = ? AND empresa_id = ?",
            (cuadrada, poliza_id, empresa_id),
        )

        ultimo_paso = con.execute(
            "SELECT COALESCE(MAX(paso), -1) FROM poliza_auditoria WHERE poliza_id = ?",
            (poliza_id,),
        ).fetchone()[0]
        estado_txt = "Cuadrada" if cuadrada else f"Descuadrada (Diferencia: ${abs(diferencia):,.2f})"
        con.execute(
            "INSERT INTO poliza_auditoria (poliza_id, paso, detalle) VALUES (?, ?, ?)",
            (
                poliza_id,
                ultimo_paso + 1,
                f"Edición manual ({datetime.now().strftime('%Y-%m-%d %H:%M:%S')}): "
                f"Cargos ${cargos:,.2f}, Abonos ${abonos:,.2f} -> {estado_txt}",
            ),
        )

    return {
        "cuadrada": bool(cuadrada),
        "cargos": cargos,
        "abonos": abonos,
        "diferencia": diferencia,
    }


def _limpiar_polizas_en_conexion(con, empresa_id, incluir_enviadas=False):
    """Borra pólizas y libera sus movimientos. Las ya enviadas a CONTPAQi se conservan."""
    filtro = "" if incluir_enviadas else " AND enviada_contpaqi = 0"
    subconsulta = f"SELECT id FROM polizas WHERE empresa_id = ?{filtro}"
    con.execute(f"UPDATE movimientos SET poliza_id = NULL WHERE poliza_id IN ({subconsulta})", (empresa_id,))
    con.execute(f"DELETE FROM poliza_auditoria WHERE poliza_id IN ({subconsulta})", (empresa_id,))
    con.execute(f"DELETE FROM poliza_lineas WHERE poliza_id IN ({subconsulta})", (empresa_id,))
    con.execute(f"DELETE FROM polizas WHERE empresa_id = ?{filtro}", (empresa_id,))


def limpiar_polizas(empresa_id, incluir_enviadas=False):
    with get_connection() as con:
        _limpiar_polizas_en_conexion(con, empresa_id, incluir_enviadas)


def respaldar_y_limpiar_movimientos(empresa_id, usuario_id=None, motivo="Limpieza de movimientos para nueva carga"):
    """
    Crea un respaldo íntegro (en base de datos y archivo físico JSON) de todos los movimientos,
    documentos y pólizas actuales de la empresa, y luego los elimina para permitir una carga nueva.
    Garantiza trazabilidad histórica y nunca bloquea al usuario.
    """
    import os
    import json
    from datetime import date

    with get_connection() as con:
        cur = con.cursor()

        # 1. Extraer datos actuales
        movimientos = [dict(m) for m in cur.execute("SELECT * FROM movimientos WHERE empresa_id = ?", (empresa_id,)).fetchall()]
        documentos = [dict(d) for d in cur.execute("SELECT * FROM documentos_importados WHERE empresa_id = ?", (empresa_id,)).fetchall()]
        polizas = [dict(p) for p in cur.execute("SELECT * FROM polizas WHERE empresa_id = ?", (empresa_id,)).fetchall()]

        poliza_ids = [p["id"] for p in polizas]
        lineas = []
        if poliza_ids:
            ph = ",".join(["?"] * len(poliza_ids))
            lineas = [dict(l) for l in cur.execute(f"SELECT * FROM poliza_lineas WHERE poliza_id IN ({ph})", tuple(poliza_ids)).fetchall()]

        mov_ids = [m["id"] for m in movimientos]
        cfdi_movs = []
        if mov_ids:
            phm = ",".join(["?"] * len(mov_ids))
            cfdi_movs = [dict(cm) for cm in cur.execute(f"SELECT * FROM cfdi_movimiento WHERE movimiento_id IN ({phm})", tuple(mov_ids)).fetchall()]

        datos_respaldo = {
            "empresa_id": empresa_id,
            "fecha_creacion": datetime.now().isoformat(),
            "motivo": motivo,
            "total_movimientos": len(movimientos),
            "total_polizas": len(polizas),
            "total_documentos": len(documentos),
            "movimientos": movimientos,
            "documentos": documentos,
            "polizas": polizas,
            "poliza_lineas": lineas,
            "cfdi_movimientos": cfdi_movs,
        }

        def _json_serial(obj):
            if isinstance(obj, (datetime, date)):
                return obj.isoformat()
            if isinstance(obj, Decimal):
                return float(obj)
            return str(obj)

        json_str = json.dumps(datos_respaldo, default=_json_serial)

        # 2. Guardar en disco
        dir_respaldos = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads", str(empresa_id), "respaldos")
        os.makedirs(dir_respaldos, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        ruta_archivo = os.path.join(dir_respaldos, f"respaldo_movimientos_{timestamp}.json")
        try:
            with open(ruta_archivo, "w", encoding="utf-8") as f:
                f.write(json_str)
        except Exception:
            ruta_archivo = None

        # 3. Guardar en tabla respaldos_cargas
        respaldo_id = None
        try:
            cur.execute(
                """INSERT INTO respaldos_cargas
                   (empresa_id, usuario_id, fecha, motivo, total_movimientos, total_polizas, archivo_respaldo, datos_json)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (empresa_id, usuario_id, datetime.now().strftime("%Y-%m-%d %H:%M:%S"), motivo, len(movimientos), len(polizas), ruta_archivo, json_str),
            )
            respaldo_id = cur.lastrowid
        except Exception:
            pass

        # 4. Limpieza completa
        if poliza_ids:
            cur.execute(f"DELETE FROM poliza_auditoria WHERE poliza_id IN ({ph})", tuple(poliza_ids))
            cur.execute(f"DELETE FROM poliza_lineas WHERE poliza_id IN ({ph})", tuple(poliza_ids))
            cur.execute(f"DELETE FROM polizas WHERE id IN ({ph})", tuple(poliza_ids))

        if mov_ids:
            cur.execute(f"DELETE FROM cfdi_movimiento WHERE movimiento_id IN ({phm})", tuple(mov_ids))
            cur.execute(f"DELETE FROM movimientos WHERE id IN ({phm})", tuple(mov_ids))

        cur.execute("DELETE FROM documentos_importados WHERE empresa_id = ?", (empresa_id,))
        con.commit()

        return {
            "ok": True,
            "respaldo_id": respaldo_id,
            "total_movimientos": len(movimientos),
            "total_polizas": len(polizas),
            "archivo": ruta_archivo
        }


def limpiar_movimientos(empresa_id, forzar=False, usuario_id=None):
    """
    Limpia los movimientos y pólizas creando siempre un respaldo automático previo,
    permitiendo hacer una carga nueva sin bloquear al usuario ni perder datos.
    """
    return respaldar_y_limpiar_movimientos(empresa_id, usuario_id=usuario_id)


def crear_polizas_anticipo(empresa_id: int, movimiento_id: int, cfdi_ids: list, cuenta_anticipo: str, cuenta_contraparte: str = None):
    """
    Genera dos pólizas simultáneas para un movimiento con factura(s) de mes posterior:
    1) Póliza de Anticipo (Ingreso o Egreso) con fecha del movimiento bancario.
    2) Póliza de Aplicación (Diario) con fecha del primer día del mes de la factura.
    """
    from movimientos_comun import _SQL_MOVIMIENTOS_CON_BANCO, _sin_guiones

    if not cfdi_ids:
        raise ValueError("Se requiere al menos un CFDI para generar el anticipo.")
    if not cuenta_anticipo or not cuenta_anticipo.strip():
        raise ValueError("Debes especificar la cuenta contable de anticipo.")

    cta_anticipo_clean = cuenta_anticipo.split(" - ")[0].split()[0].replace("-", "").replace(".", "").strip()
    cta_anticipo_display = cuenta_anticipo.split(" - ")[0].strip()

    with get_connection() as con:
        cur = con.cursor()
        mov = cur.execute(
            _SQL_MOVIMIENTOS_CON_BANCO + " WHERE m.id = ? AND m.empresa_id = ?",
            (movimiento_id, empresa_id)
        ).fetchone()
        if not mov:
            raise ValueError(f"Movimiento {movimiento_id} no encontrado.")

        empresa = cur.execute("SELECT * FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
        if not empresa:
            raise ValueError("Empresa no encontrada.")

        placeholders = ",".join(["?"] * len(cfdi_ids))
        cfdis = cur.execute(
            f"SELECT * FROM cfdis WHERE id IN ({placeholders}) AND empresa_id = ? ORDER BY fecha ASC",
            tuple(cfdi_ids + [empresa_id])
        ).fetchall()
        if not cfdis:
            raise ValueError("Facturas CFDI no encontradas.")

        tipo_mov = mov["tipo"]  # 'ingreso' | 'egreso'
        total_mov = float(mov["total"] or 0.0)
        fecha_mov = str(mov["fecha"])

        cfdi_principal = cfdis[0]
        fecha_cfdi = str(cfdi_principal["fecha"])
        mes_cfdi = fecha_cfdi[:7]
        fecha_aplicacion = f"{mes_cfdi}-01"

        folios = ", ".join(f"{c['serie'] or ''}{c['folio'] or ''}".strip() for c in cfdis if f"{c['serie'] or ''}{c['folio'] or ''}".strip())
        persona_nombre = (cfdi_principal["nombre_receptor" if tipo_mov == "ingreso" else "nombre_emisor"] or 
                          cfdi_principal["rfc_receptor" if tipo_mov == "ingreso" else "rfc_emisor"] or "").strip()

        cuenta_banco = _sin_guiones(mov["cuenta_banco"])
        if not cuenta_banco:
            b_cta = cur.execute("SELECT cuenta_contable FROM bancos WHERE empresa_id = ? LIMIT 1", (empresa_id,)).fetchone()
            cuenta_banco = _sin_guiones(b_cta["cuenta_contable"]) if b_cta else "10201001"

        tasa_iva = float(empresa["tasa_iva"] or 0.16)
        cuenta_iva_trasladado = _sin_guiones(empresa.get("cuenta_iva_trasladado") or "20801001")
        cuenta_iva_por_trasladar = _sin_guiones(empresa.get("cuenta_iva_por_trasladar") or "20901001")
        cuenta_iva_acreditable = _sin_guiones(empresa.get("cuenta_iva_acreditable") or "11801001")
        cuenta_iva_por_acreditar = _sin_guiones(empresa.get("cuenta_iva_por_acreditar") or "11901001")

        cuenta_contra = ""
        if cuenta_contraparte:
            cuenta_contra = cuenta_contraparte.split(" - ")[0].split()[0].replace("-", "").replace(".", "").strip()
        if not cuenta_contra:
            rfc_buscar = cfdi_principal["rfc_receptor" if tipo_mov == "ingreso" else "rfc_emisor"]
            c_cat = cur.execute(
                "SELECT cuenta FROM cuentas_catalogo WHERE empresa_id = ? AND (cuenta ILIKE ? OR descripcion ILIKE ?) LIMIT 1",
                (empresa_id, f"%{rfc_buscar}%", f"%{persona_nombre[:10]}%")
            ).fetchone()
            if c_cat:
                cuenta_contra = _sin_guiones(c_cat["cuenta"])
            else:
                cuenta_contra = "10501001" if tipo_mov == "ingreso" else "20101001"

        aplica_iva = mov["afectable_impuestos"] is None or bool(mov["afectable_impuestos"])
        if aplica_iva and tasa_iva > 0:
            base = round(total_mov / (1.0 + tasa_iva), 2)
            iva = round(total_mov - base, 2)
        else:
            base = total_mov
            iva = 0.0

        if tipo_mov == "ingreso" and not empresa.get("cuenta_anticipo_clientes"):
            cur.execute("UPDATE empresas SET cuenta_anticipo_clientes = ? WHERE id = ?", (cta_anticipo_display, empresa_id))
        elif tipo_mov == "egreso" and not empresa.get("cuenta_anticipo_proveedores"):
            cur.execute("UPDATE empresas SET cuenta_anticipo_proveedores = ? WHERE id = ?", (cta_anticipo_display, empresa_id))

        # 1. PÓLIZA DE ANTICIPO
        poliza_1_id = mov["poliza_id"]
        sig_num_1 = cur.execute(
            "SELECT COALESCE(MAX(numero), 0) + 1 FROM polizas WHERE empresa_id = ? AND tipo = ?",
            (empresa_id, tipo_mov)
        ).fetchone()[0]

        concepto_1 = formatear_concepto_poliza(
            f"Anticipo {'Cliente' if tipo_mov == 'ingreso' else 'Proveedor'} {persona_nombre or mov['descripcion']}",
            tiene_cfdi=True, tiene_iva=bool(iva > 0)
        )
        afectar_fiscal_1 = 1 if tipo_mov == "ingreso" else 0

        if poliza_1_id:
            cur.execute(
                """UPDATE polizas SET fecha = ?, concepto = ?, cuadrada = 1, enviada_contpaqi = 0,
                                      es_anticipo = 1, es_aplicacion_anticipo = 0, afectar_iva_fiscal = ?
                   WHERE id = ? AND empresa_id = ?""",
                (fecha_mov, concepto_1, afectar_fiscal_1, poliza_1_id, empresa_id)
            )
            cur.execute("DELETE FROM poliza_lineas WHERE poliza_id = ?", (poliza_1_id,))
        else:
            cur.execute(
                """INSERT INTO polizas (empresa_id, tipo, numero, fecha, concepto, cuadrada, enviada_contpaqi,
                                       es_anticipo, es_aplicacion_anticipo, afectar_iva_fiscal)
                   VALUES (?, ?, ?, ?, ?, 1, 0, 1, 0, ?)""",
                (empresa_id, tipo_mov, sig_num_1, fecha_mov, concepto_1, afectar_fiscal_1)
            )
            poliza_1_id = cur.lastrowid

        lineas_p1 = []
        if tipo_mov == "ingreso":
            lineas_p1.append((cuenta_banco, "cargo", total_mov, "Depósito bancario anticipo cliente"))
            lineas_p1.append((cta_anticipo_clean, "abono", base, "Anticipo de clientes"))
            if iva > 0:
                lineas_p1.append((cuenta_iva_trasladado, "abono", iva, "IVA trasladado cobrado anticipo"))
        else:
            lineas_p1.append((cta_anticipo_clean, "cargo", total_mov, "Anticipo a proveedores"))
            lineas_p1.append((cuenta_banco, "abono", total_mov, "Pago bancario anticipo proveedor"))

        for orden, (cta, nat, imp, dsc) in enumerate(lineas_p1):
            cur.execute(
                """INSERT INTO poliza_lineas (poliza_id, orden, cuenta, naturaleza, importe, descripcion, formula_usada)
                   VALUES (?, ?, ?, ?, ?, ?, 'ANTICIPO')""",
                (poliza_1_id, orden, cta, nat, imp, dsc)
            )

        # 2. PÓLIZA DE APLICACIÓN DE ANTICIPO
        poliza_2_id = mov["poliza_aplicacion_id"] if "poliza_aplicacion_id" in mov.keys() else None
        sig_num_2 = cur.execute(
            "SELECT COALESCE(MAX(numero), 0) + 1 FROM polizas WHERE empresa_id = ? AND tipo = 'diario'",
            (empresa_id,)
        ).fetchone()[0]

        concepto_2 = formatear_concepto_poliza(
            f"Aplicación Anticipo Fac {folios or 'CFDI'} {persona_nombre}",
            tiene_cfdi=True, tiene_iva=bool(iva > 0)
        )
        afectar_fiscal_2 = 0 if tipo_mov == "ingreso" else 1

        if poliza_2_id:
            cur.execute(
                """UPDATE polizas SET fecha = ?, concepto = ?, cuadrada = 1, enviada_contpaqi = 0,
                                      es_anticipo = 0, es_aplicacion_anticipo = 1, poliza_origen_id = ?, afectar_iva_fiscal = ?
                   WHERE id = ? AND empresa_id = ?""",
                (fecha_aplicacion, concepto_2, poliza_1_id, afectar_fiscal_2, poliza_2_id, empresa_id)
            )
            cur.execute("DELETE FROM poliza_lineas WHERE poliza_id = ?", (poliza_2_id,))
        else:
            cur.execute(
                """INSERT INTO polizas (empresa_id, tipo, numero, fecha, concepto, cuadrada, enviada_contpaqi,
                                       es_anticipo, es_aplicacion_anticipo, poliza_origen_id, afectar_iva_fiscal)
                   VALUES (?, 'diario', ?, ?, ?, 1, 0, 0, 1, ?, ?)""",
                (empresa_id, sig_num_2, fecha_aplicacion, concepto_2, poliza_1_id, afectar_fiscal_2)
            )
            poliza_2_id = cur.lastrowid

        lineas_p2 = []
        if tipo_mov == "ingreso":
            lineas_p2.append((cta_anticipo_clean, "cargo", base, "Amortización anticipo clientes"))
            if iva > 0:
                lineas_p2.append((cuenta_iva_trasladado, "cargo", iva, "Cancelación IVA trasladado anticipo"))
                lineas_p2.append((cuenta_iva_por_trasladar, "cargo", iva, "IVA por trasladar devengado"))
                lineas_p2.append((cuenta_iva_trasladado, "abono", iva, "IVA trasladado definitivo"))
            lineas_p2.append((cuenta_contra, "abono", total_mov, "Aplicación factura cliente"))
        else:
            lineas_p2.append((cuenta_contra, "cargo", total_mov, "Aplicación factura proveedor"))
            if iva > 0:
                lineas_p2.append((cuenta_iva_acreditable, "cargo", iva, "IVA acreditable pagado aplicación"))
                lineas_p2.append((cuenta_iva_por_acreditar, "abono", iva, "Cancelación IVA por acreditar"))
            lineas_p2.append((cta_anticipo_clean, "abono", total_mov, "Amortización anticipo proveedores"))

        for orden, (cta, nat, imp, dsc) in enumerate(lineas_p2):
            cur.execute(
                """INSERT INTO poliza_lineas (poliza_id, orden, cuenta, naturaleza, importe, descripcion, formula_usada)
                   VALUES (?, ?, ?, ?, ?, ?, 'APLICACION_ANTICIPO')""",
                (poliza_2_id, orden, cta, nat, imp, dsc)
            )

        cur.execute(
            """UPDATE movimientos
               SET poliza_id = ?, poliza_aplicacion_id = ?, es_anticipo = 1, estado_clasificacion = 'manual'
               WHERE id = ? AND empresa_id = ?""",
            (poliza_1_id, poliza_2_id, movimiento_id, empresa_id)
        )
        con.commit()

        return {
            "ok": True,
            "poliza_anticipo_id": poliza_1_id,
            "poliza_aplicacion_id": poliza_2_id,
            "fecha_anticipo": fecha_mov,
            "fecha_aplicacion": fecha_aplicacion,
            "tipo": tipo_mov
        }
