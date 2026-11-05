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
import os
import sys
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "app", "core"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "app", "exporters"))

from rule_engine import encontrar_regla, normalizar_texto
from learning_engine import sugerir_regla_desde_clasificacion_manual
from policy_generator import generar_poliza
from formula_engine import a_decimal, redondear_moneda, MontoDecimal
from rules_repository import RepositorioReglas
from contpaqi_exporter import MovimientoPoliza, ImpuestoPoliza, FacturaAplicada
from contpaqi_txt_exporter import exportar_polizas_contpaqi_txt
from licencia import verificar_licencia_empresa, LicenciaInvalida
from ml_engine import EntrenadorML

from db import get_connection

def insertar_movimientos(empresa_id, documento_id, movimientos):
    ids = []
    with get_connection() as con:
        cur = con.cursor()
        for mov in movimientos:
            cur.execute(
                """INSERT INTO movimientos
                   (empresa_id, documento_id, fecha, descripcion, descripcion_normalizada,
                    tipo, total, rfc_contraparte, cuenta_bancaria_contraparte,
                    referencia_bancaria, numero_factura, fila_original, estado_clasificacion)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pendiente')""",
                (
                    empresa_id,
                    documento_id,
                    mov["fecha"].strftime("%Y-%m-%d") if hasattr(mov["fecha"], "strftime") else str(mov["fecha"])[:10],
                    mov["descripcion"],
                    normalizar_texto(mov["descripcion"]),
                    mov["tipo"],
                    redondear_moneda(mov["total"]),
                    mov.get("rfc_contraparte"),
                    mov.get("cuenta_bancaria_contraparte"),
                    mov.get("referencia_bancaria"),
                    mov.get("numero_factura") or "",
                    mov.get("fila_original"),
                ),
            )
            ids.append(cur.lastrowid)
    return ids

def registrar_memoria_ml_exitosa(empresa_id, descripcion, tipo, regla_id, con=None):
    sql = """INSERT INTO memoria_ml (empresa_id, descripcion_limpia, tipo, regla_asignada_id)
             VALUES (?, ?, ?, ?)
             ON CONFLICT(empresa_id, descripcion_limpia, tipo, regla_asignada_id)
             DO UPDATE SET frecuencia = memoria_ml.frecuencia + 1, ultima_vez = CURRENT_TIMESTAMP"""
    params = (empresa_id, str(descripcion).strip(), str(tipo).strip(), regla_id)
    if con is not None:
        con.execute(sql, params)
        return
    with get_connection() as con_propia:
        con_propia.execute(sql, params)

def entrenar_modelo_empresa(empresa_id):
    with get_connection() as con:
        datos = con.execute(
            "SELECT descripcion_limpia as descripcion, tipo, regla_asignada_id as regla_id FROM memoria_ml WHERE empresa_id = ?",
            (empresa_id,),
        ).fetchall()
    if datos:
        EntrenadorML.entrenar(empresa_id, [dict(d) for d in datos])

def _fila_a_movimiento_dict(fila):
    return {
        "id": fila["id"],
        "empresa_id": fila["empresa_id"],
        "fecha": datetime.strptime(fila["fecha"], "%Y-%m-%d"),
        "descripcion": fila["descripcion"],
        "tipo": fila["tipo"],
        "total": redondear_moneda(fila["total"]),
        "rfc_contraparte": fila["rfc_contraparte"],
        "cuenta_bancaria_contraparte": fila["cuenta_bancaria_contraparte"],
        "numero_factura": fila["numero_factura"],
        "cuenta_banco": fila.get("cuenta_banco"),
        "tiene_iva": True,
        "ret_iva": MontoDecimal("0.00"),
        "ret_isr": MontoDecimal("0.00"),
        "tipo_cambio": a_decimal(fila.get("tipo_cambio", "1.0"), "1.0"),
    }

class TotalPolizasGeneradas(int):
    """Entero que conserva la lista de movimientos que no cuadraron al generar pólizas."""
    def __new__(cls, valor, descuadres=None):
        instancia = super().__new__(cls, int(valor))
        instancia.descuadres = list(descuadres or [])
        return instancia

def _aplicar_impuestos_cfdi_a_movimiento(mov, cfdis_asoc):
    """Aplica el IVA y retenciones exactos proporcionales de los CFDIs confirmados."""
    if not cfdis_asoc:
        return
    iva_exacto_tot = MontoDecimal("0.00")
    ret_iva_tot = MontoDecimal("0.00")
    ret_isr_tot = MontoDecimal("0.00")
    for c_asoc in cfdis_asoc:
        cfdi_tot = a_decimal(c_asoc["cfdi_total"])
        if cfdi_tot > Decimal("0.00"):
            prop = min(Decimal("1.0"), a_decimal(c_asoc["importe_aplicado"]) / cfdi_tot)
            iva_exacto_tot += a_decimal(c_asoc.get("iva_cfdi", 0)) * prop
            ret_iva_tot += a_decimal(c_asoc.get("ret_iva_cfdi", 0)) * prop
            ret_isr_tot += a_decimal(c_asoc.get("ret_isr_cfdi", 0)) * prop
    mov["iva_exacto"] = redondear_moneda(iva_exacto_tot)
    if ret_iva_tot > Decimal("0.00"):
        mov["ret_iva"] = redondear_moneda(ret_iva_tot)
    if ret_isr_tot > Decimal("0.00"):
        mov["ret_isr"] = redondear_moneda(ret_isr_tot)
    mov["tiene_iva"] = True

def _construir_info_descuadre(mov, nombre_regla, resultado):
    cargos = redondear_moneda(
        sum((l.importe for l in resultado.lineas if l.naturaleza == "cargo"), MontoDecimal("0.00"))
    )
    abonos = redondear_moneda(
        sum((l.importe for l in resultado.lineas if l.naturaleza == "abono"), MontoDecimal("0.00"))
    )
    if resultado.errores:
        motivo = "; ".join(resultado.errores)
    else:
        motivo = (
            f"Cargos (${cargos:,.2f}) != Abonos (${abonos:,.2f}), "
            f"diferencia de ${abs(resultado.diferencia):,.2f}"
        )
    mensaje = (
        f"Movimiento #{mov['id']} ({str(mov.get('descripcion', ''))[:45]} - "
        f"${redondear_moneda(mov['total']):,.2f}) no cuadró con la regla '{nombre_regla}': {motivo}."
    )
    return {
        "movimiento_id": mov["id"],
        "descripcion": mov.get("descripcion", ""),
        "total": redondear_moneda(mov["total"]),
        "regla": nombre_regla,
        "cargos": cargos,
        "abonos": abonos,
        "diferencia": resultado.diferencia,
        "motivo": motivo,
        "mensaje": mensaje,
    }

def clasificar_pendientes(empresa_id):
    """Corre el motor de reglas sobre los movimientos pendientes de la
    empresa y actualiza su estado usando una única conexión al pool.
    
    Criterio Contable Seguro:
    - Certeza contable (RFC exacto, cuenta bancaria exacta, descripción exacta
      o palabra clave específica con confianza >= 90%): pasa a 'automatico'.
    - Coincidencia difusa o palabra clave corta (< 90%): pasa a 'sugerido'
      para revisión y autorización explícita del contador sin generar póliza en falso.
    """
    repo = RepositorioReglas()
    reglas, plantillas = repo.cargar_para_motor(empresa_id)

    automaticos = 0
    sugeridos = 0
    usos_por_regla = {}
    descuadres = []

    with get_connection() as con:
        emp_cfg = con.execute(
            "SELECT tasa_iva, tasa_retencion_iva, tasa_retencion_isr FROM empresas WHERE id = ?",
            (empresa_id,),
        ).fetchone()
        if emp_cfg:
            tasa_iva = a_decimal(emp_cfg["tasa_iva"], "0.16")
            tasa_ret_iva = a_decimal(emp_cfg["tasa_retencion_iva"], "0.0")
            tasa_ret_isr = a_decimal(emp_cfg["tasa_retencion_isr"], "0.0")
        else:
            tasa_iva = MontoDecimal("0.16")
            tasa_ret_iva = MontoDecimal("0.0")
            tasa_ret_isr = MontoDecimal("0.0")

        pendientes = con.execute(
            "SELECT * FROM movimientos WHERE empresa_id = ? AND estado_clasificacion IN ('pendiente', 'sugerido')",
            (empresa_id,),
        ).fetchall()

        cur = con.cursor()
        pendientes_ids = [str(int(f["id"])) for f in pendientes]
        cfdis_map: dict[int, list] = {}
        if pendientes_ids:
            cur.execute(f"""
                SELECT cm.movimiento_id, c.total AS cfdi_total, cm.importe_aplicado,
                       COALESCE((SELECT SUM(importe) FROM cfdi_impuestos ci WHERE ci.cfdi_id = c.id AND ci.es_retencion = 0 AND ci.tipo_impuesto = 'IVA'), 0) AS iva_cfdi,
                       COALESCE((SELECT SUM(importe) FROM cfdi_impuestos ci WHERE ci.cfdi_id = c.id AND ci.es_retencion = 1 AND ci.tipo_impuesto = 'IVA'), 0) AS ret_iva_cfdi,
                       COALESCE((SELECT SUM(importe) FROM cfdi_impuestos ci WHERE ci.cfdi_id = c.id AND ci.es_retencion = 1 AND ci.tipo_impuesto = 'ISR'), 0) AS ret_isr_cfdi
                FROM cfdis c
                JOIN cfdi_movimiento cm ON cm.cfdi_id = c.id
                WHERE cm.movimiento_id IN ({','.join(pendientes_ids)}) AND cm.confirmado = 1
            """)
            for row in cur.fetchall():
                cfdis_map.setdefault(row["movimiento_id"], []).append(row)

        for fila in pendientes:
            mov = _fila_a_movimiento_dict(fila)
            match = encontrar_regla(mov, reglas)
            if match.regla is None:
                continue

            cfdis_asoc = cfdis_map.get(fila["id"], [])
            _aplicar_impuestos_cfdi_a_movimiento(mov, cfdis_asoc)

            resultado = generar_poliza(
                movimiento=mov,
                plantilla=plantillas[match.regla.id],
                nombre_regla=match.regla.nombre,
                motivo_match=match.motivo,
                tasa_iva=tasa_iva,
                tasa_ret_iva=tasa_ret_iva,
                tasa_ret_isr=tasa_ret_isr,
            )
            if not resultado.cuadrada:
                descuadres.append(_construir_info_descuadre(mov, match.regla.nombre, resultado))
                continue

            # Determinación de certeza contable
            es_certeza = False
            if match.nivel in ("rfc", "cuenta_bancaria", "exacta"):
                es_certeza = True
            elif match.nivel == "contiene" and match.confianza >= 90:
                es_certeza = True

            if es_certeza:
                con.execute(
                    """UPDATE movimientos
                       SET regla_id = ?, confianza_clasificacion = ?, estado_clasificacion = 'automatico'
                       WHERE id = ?""",
                    (match.regla.id, match.confianza, fila["id"]),
                )
                usos_por_regla[match.regla.id] = usos_por_regla.get(match.regla.id, 0) + 1
                automaticos += 1
                registrar_memoria_ml_exitosa(empresa_id, mov["descripcion"], mov["tipo"], match.regla.id, con=con)
            else:
                con.execute(
                    """UPDATE movimientos
                       SET regla_id = ?, confianza_clasificacion = ?, estado_clasificacion = 'sugerido'
                       WHERE id = ?""",
                    (match.regla.id, match.confianza, fila["id"]),
                )
                sugeridos += 1

        for regla_id, veces in usos_por_regla.items():
            con.execute(
                "UPDATE reglas SET veces_aplicada = veces_aplicada + ? WHERE id = ?",
                (veces, regla_id),
            )

    return {
        "automaticos": automaticos,
        "sugeridos": sugeridos,
        "pendientes_restantes": len(pendientes) - automaticos - sugeridos,
        "descuadres": descuadres,
    }

def listar_movimientos(empresa_id, estado=None):
    with get_connection() as con:
        query = "SELECT * FROM movimientos WHERE empresa_id = ?"
        params = [empresa_id]
        if estado:
            query += " AND estado_clasificacion = ?"
            params.append(estado)
        query += " ORDER BY fecha DESC, id DESC"
        filas = con.execute(query, params).fetchall()
    return filas

def sugerencia_para_movimiento(movimiento_id, empresa_id=None):
    with get_connection() as con:
        if empresa_id is not None:
            fila = con.execute(
                "SELECT * FROM movimientos WHERE id = ? AND empresa_id = ?",
                (movimiento_id, empresa_id),
            ).fetchone()
        else:
            fila = con.execute("SELECT * FROM movimientos WHERE id = ?", (movimiento_id,)).fetchone()
        if not fila:
            return None, None
        empresa = con.execute("SELECT rfc FROM empresas WHERE id = ?", (fila["empresa_id"],)).fetchone()

    mov = _fila_a_movimiento_dict(fila)
    rfc_empresa = empresa["rfc"] if empresa else ""
    return mov, sugerir_regla_desde_clasificacion_manual(mov, rfc_empresa=rfc_empresa)

def generar_polizas_pendientes(empresa_id, inicio_ingreso=None, inicio_egreso=None):
    """Genera pólizas para todos los movimientos ya clasificados
    (estado 'automatico' o 'manual') que todavía no tienen póliza."""
    repo = RepositorioReglas()
    _, plantillas = repo.cargar_para_motor(empresa_id)

    generadas = 0
    descuadres = []
    with get_connection() as con:
        emp_cfg = con.execute(
            "SELECT nombre, tasa_iva, tasa_retencion_iva, tasa_retencion_isr FROM empresas WHERE id = ?",
            (empresa_id,),
        ).fetchone()
        verificar_licencia_empresa(emp_cfg["nombre"] if emp_cfg else "")

        if emp_cfg:
            tasa_iva = a_decimal(emp_cfg["tasa_iva"], "0.16")
            tasa_ret_iva = a_decimal(emp_cfg["tasa_retencion_iva"], "0.0")
            tasa_ret_isr = a_decimal(emp_cfg["tasa_retencion_isr"], "0.0")
        else:
            tasa_iva = MontoDecimal("0.16")
            tasa_ret_iva = MontoDecimal("0.0")
            tasa_ret_isr = MontoDecimal("0.0")

        movimientos = con.execute(
            """SELECT m.*, b.cuenta_contable AS cuenta_banco
               FROM movimientos m
               LEFT JOIN documentos_importados d ON d.id = m.documento_id
               LEFT JOIN bancos b ON b.id = d.banco_id
               WHERE m.empresa_id = ? AND m.poliza_id IS NULL
               AND m.estado_clasificacion IN ('automatico', 'manual')""",
            (empresa_id,),
        ).fetchall()

        ultimo_ingreso = con.execute(
            "SELECT COALESCE(MAX(numero), 0) FROM polizas WHERE empresa_id = ? AND tipo = 'ingreso'",
            (empresa_id,),
        ).fetchone()[0]
        ultimo_egreso = con.execute(
            "SELECT COALESCE(MAX(numero), 0) FROM polizas WHERE empresa_id = ? AND tipo = 'egreso'",
            (empresa_id,),
        ).fetchone()[0]

        cur = con.cursor()
        # Lock transaccional a nivel de empresa para numeración serializada en PostgreSQL
        try:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext('poliza_num_' || ?::text))", (empresa_id,))
        except Exception:
            pass

        for fila in movimientos:
            if fila["regla_id"] is None:
                continue
            plantilla = plantillas.get(fila["regla_id"])
            if not plantilla:
                continue

            mov = _fila_a_movimiento_dict(fila)
            cur.execute("SELECT nombre FROM reglas WHERE id = ?", (fila["regla_id"],))
            regla_row = cur.fetchone()
            nombre_regla = regla_row["nombre"] if regla_row else "Regla"

            # Buscar CFDIs asociados al movimiento ANTES de generar la póliza
            cur.execute("""
                SELECT c.folio, c.uuid, c.nombre_emisor, c.nombre_receptor, c.rfc_emisor, c.rfc_receptor,
                       c.total AS cfdi_total, cm.importe_aplicado,
                       COALESCE((SELECT SUM(importe) FROM cfdi_impuestos ci WHERE ci.cfdi_id = c.id AND ci.es_retencion = 0 AND ci.tipo_impuesto = 'IVA'), 0) AS iva_cfdi,
                       COALESCE((SELECT SUM(importe) FROM cfdi_impuestos ci WHERE ci.cfdi_id = c.id AND ci.es_retencion = 1 AND ci.tipo_impuesto = 'IVA'), 0) AS ret_iva_cfdi,
                       COALESCE((SELECT SUM(importe) FROM cfdi_impuestos ci WHERE ci.cfdi_id = c.id AND ci.es_retencion = 1 AND ci.tipo_impuesto = 'ISR'), 0) AS ret_isr_cfdi
                FROM cfdis c
                JOIN cfdi_movimiento cm ON cm.cfdi_id = c.id
                WHERE cm.movimiento_id = ? AND cm.confirmado = 1
                ORDER BY cm.importe_aplicado DESC
            """, (fila["id"],))
            cfdis_asociados = cur.fetchall()
            _aplicar_impuestos_cfdi_a_movimiento(mov, cfdis_asociados)

            p_referencia = mov.get("numero_factura") or ""
            p_concepto = fila["descripcion"]

            if cfdis_asociados:
                if len(cfdis_asociados) > 1:
                    folios = []
                    for c_asoc in cfdis_asociados:
                        if c_asoc["folio"] and c_asoc["folio"].strip():
                            folios.append(c_asoc["folio"].strip())
                        else:
                            folios.append("U" + c_asoc["uuid"][-4:])
                    p_referencia = ", ".join(folios)[:50]
                else:
                    cfdi_asoc = cfdis_asociados[0]
                    if cfdi_asoc["folio"] and cfdi_asoc["folio"].strip():
                        p_referencia = cfdi_asoc["folio"].strip()
                    else:
                        p_referencia = "UUID: " + cfdi_asoc["uuid"][-5:]

                cfdi_principal = cfdis_asociados[0]
                if fila["tipo"] == "egreso":
                    p_concepto = cfdi_principal["nombre_emisor"] or cfdi_principal["rfc_emisor"]
                else:
                    p_concepto = cfdi_principal["nombre_receptor"] or cfdi_principal["rfc_receptor"]

            mov["descripcion"] = p_concepto
            mov["numero_factura"] = p_referencia

            resultado = generar_poliza(
                movimiento=mov,
                plantilla=plantilla,
                nombre_regla=nombre_regla,
                motivo_match="Reclasificación al generar póliza.",
                tasa_iva=tasa_iva,
                tasa_ret_iva=tasa_ret_iva,
                tasa_ret_isr=tasa_ret_isr,
            )
            if not resultado.cuadrada:
                descuadres.append(_construir_info_descuadre(mov, nombre_regla, resultado))
                continue

            # Asignación atómica del número de póliza evitando colisión concurrente (A6)
            for intento_num in range(10):
                cur.execute(
                    "SELECT COALESCE(MAX(numero), 0) + 1 FROM polizas WHERE empresa_id = ? AND tipo = ?",
                    (empresa_id, fila["tipo"]),
                )
                numero = cur.fetchone()[0]
                try:
                    cur.execute(
                        """INSERT INTO polizas (empresa_id, tipo, numero, fecha, referencia, concepto, cuadrada, enviada_contpaqi)
                           VALUES (?, ?, ?, ?, ?, ?, 1, 0)""",
                        (empresa_id, fila["tipo"], numero, fila["fecha"], p_referencia, p_concepto),
                    )
                    poliza_id = cur.lastrowid
                    break
                except Exception as e_num:
                    if "unique" in str(e_num).lower() and intento_num < 9:
                        continue
                    raise

            for orden, linea in enumerate(resultado.lineas):
                cur.execute(
                    """INSERT INTO poliza_lineas
                       (poliza_id, orden, cuenta, naturaleza, importe, descripcion, formula_usada)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        poliza_id,
                        orden,
                        linea.cuenta,
                        linea.naturaleza,
                        redondear_moneda(linea.importe),
                        linea.descripcion,
                        linea.formula_usada,
                    ),
                )

            for paso, texto in enumerate(resultado.explicacion):
                cur.execute(
                    "INSERT INTO poliza_auditoria (poliza_id, paso, detalle) VALUES (?, ?, ?)",
                    (poliza_id, paso, texto),
                )

            cur.execute("UPDATE movimientos SET poliza_id = ? WHERE id = ?", (poliza_id, fila["id"]))
            generadas += 1

    return TotalPolizasGeneradas(generadas, descuadres=descuadres)

def exportar_polizas(empresa_id, ruta_salida):
    with get_connection() as con:
        empresa = con.execute("SELECT * FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
        verificar_licencia_empresa(empresa["nombre"] if empresa else "")

        polizas = con.execute(
            "SELECT * FROM polizas WHERE empresa_id = ? ORDER BY tipo, numero", (empresa_id,)
        ).fetchall()

        movimientos_poliza = []
        map_lineas = {}
        map_movs = {}
        if polizas:
            p_ids = [str(int(p["id"])) for p in polizas]
            placeholders = ",".join(p_ids)

            todas_lineas = con.execute(
                f"SELECT * FROM poliza_lineas WHERE poliza_id IN ({placeholders}) ORDER BY poliza_id, orden"
            ).fetchall()
            for l in todas_lineas:
                map_lineas.setdefault(l["poliza_id"], []).append(l)

            todos_movs = con.execute(f"""
                SELECT m.*, b.cuenta_contable AS cuenta_banco
                FROM movimientos m
                LEFT JOIN documentos_importados d ON d.id = m.documento_id
                LEFT JOIN bancos b ON b.id = d.banco_id
                WHERE m.poliza_id IN ({placeholders})
            """).fetchall()
            for m in todos_movs:
                map_movs[m["poliza_id"]] = m

        for p in polizas:
            lineas_db = map_lineas.get(p["id"], [])
            mov_db = map_movs.get(p["id"])

            class _Linea:
                def __init__(self, fila):
                    self.cuenta = fila["cuenta"]
                    self.naturaleza = fila["naturaleza"]
                    self.importe = redondear_moneda(fila["importe"])
                    self.descripcion = fila["descripcion"]

            lineas = [_Linea(f) for f in lineas_db]

            cfdi_filas = con.execute(
                """SELECT cm.importe_aplicado, c.id AS cfdi_id, c.uuid, c.serie, c.folio,
                          c.total AS cfdi_total, c.tipo AS cfdi_tipo,
                          c.rfc_emisor, c.rfc_receptor, c.uuids_relacionados
                   FROM cfdi_movimiento cm JOIN cfdis c ON c.id = cm.cfdi_id
                   WHERE cm.movimiento_id = ? AND cm.confirmado = 1""",
                (mov_db["id"] if mov_db else -1,),
            ).fetchall()

            cfdi_uuid = cfdi_serie = cfdi_folio = None
            cfdi_impuestos = []
            facturas = []
            cuenta_banco_mov = None

            cuenta_banco_real = mov_db["cuenta_banco"] if mov_db and mov_db["cuenta_banco"] else "1020001"

            if cfdi_filas:
                cuenta_banco_mov = cuenta_banco_real.replace("-", "")

                for cf in cfdi_filas:
                    uuid_final = cf["uuid"]
                    if cf.get("uuids_relacionados"):
                        uuids_lista = cf["uuids_relacionados"].split(",")
                        if uuids_lista and uuids_lista[0].strip():
                            uuid_final = uuids_lista[0].strip()

                    cfdi_tot = a_decimal(cf["cfdi_total"])
                    factor = (a_decimal(cf["importe_aplicado"]) / cfdi_tot) if cfdi_tot else MontoDecimal("1.0")
                    impuestos_db = con.execute(
                        "SELECT * FROM cfdi_impuestos WHERE cfdi_id = ?", (cf["cfdi_id"],)
                    ).fetchall()
                    impuestos_factura = [
                        ImpuestoPoliza(
                            base=redondear_moneda(a_decimal(i["base"]) * factor),
                            importe=redondear_moneda(a_decimal(i["importe"]) * factor),
                            tasa=a_decimal(i["tasa"]),
                            es_retencion=bool(i["es_retencion"]),
                            tipo_impuesto=i["tipo_impuesto"] or "IVA",
                        )
                        for i in impuestos_db
                    ]
                    rfc_persona = (
                        cf["rfc_emisor"] if cf["cfdi_tipo"] == "recibido" else cf["rfc_receptor"]
                    ) or ""
                    facturas.append(FacturaAplicada(
                        uuid=uuid_final,
                        serie=cf["serie"] or "",
                        folio=cf["folio"] or "",
                        rfc_persona=rfc_persona,
                        impuestos=impuestos_factura,
                    ))
                    cfdi_impuestos.extend(impuestos_factura)

                if len(facturas) == 1:
                    cfdi_uuid, cfdi_serie, cfdi_folio = facturas[0].uuid, facturas[0].serie, facturas[0].folio
            elif mov_db and (mov_db["afectable_impuestos"] is None or mov_db["afectable_impuestos"]):
                cuenta_banco_mov = cuenta_banco_real.replace("-", "")
                cuenta_iva_esperada = (
                    empresa["cuenta_iva_acreditable"] if p["tipo"] == "egreso"
                    else empresa["cuenta_iva_trasladado"]
                ) if empresa else None
                cuenta_iva_esperada = str(cuenta_iva_esperada or "").replace("-", "")

                linea_iva = next(
                    (l for l in lineas if str(l.cuenta).replace("-", "") == cuenta_iva_esperada
                     and cuenta_iva_esperada),
                    None,
                )
                total_mov = redondear_moneda(mov_db["total"])
                if linea_iva:
                    importe_iva = redondear_moneda(linea_iva.importe)
                    cfdi_impuestos.append(ImpuestoPoliza(
                        base=redondear_moneda(total_mov - importe_iva),
                        importe=importe_iva,
                        tasa=a_decimal(empresa["tasa_iva"], "0.16") if empresa else MontoDecimal("0.16"),
                    ))
                else:
                    cfdi_impuestos.append(ImpuestoPoliza(
                        base=total_mov,
                        importe=MontoDecimal("0.00"),
                        tasa=MontoDecimal("0.0"),
                    ))

            movimientos_poliza.append(MovimientoPoliza(
                numero_poliza=p["numero"],
                tipo=p["tipo"],
                fecha=datetime.strptime(p["fecha"], "%Y-%m-%d"),
                descripcion=p.get("concepto") or (mov_db["descripcion"] if mov_db else ""),
                lineas=lineas,
                tiene_iva=bool(cfdi_uuid or facturas),
                numero_factura=p["referencia"] or "",
                cfdi_uuid=cfdi_uuid,
                cfdi_serie=cfdi_serie,
                cfdi_folio=cfdi_folio,
                cfdi_impuestos=cfdi_impuestos,
                facturas=facturas,
                cuenta_banco=cuenta_banco_mov,
            ))

    return exportar_polizas_contpaqi_txt(
        movimientos_poliza,
        ruta_salida,
        nombre_empresa=empresa["nombre"] if empresa else None,
    )

def actualizar_poliza(poliza_id, lineas, empresa_id=None):
    """Actualiza las líneas de una póliza existente y recalcula su cuadre."""
    if not lineas or not isinstance(lineas, list):
        raise ValueError("La póliza debe tener al menos una línea.")

    with get_connection() as con:
        if empresa_id is not None:
            pol = con.execute(
                "SELECT id FROM polizas WHERE id = ? AND empresa_id = ?",
                (poliza_id, empresa_id),
            ).fetchone()
        else:
            pol = con.execute("SELECT id FROM polizas WHERE id = ?", (poliza_id,)).fetchone()
        if not pol:
            raise ValueError("Póliza no encontrada.")

        con.execute("DELETE FROM poliza_lineas WHERE poliza_id = ?", (poliza_id,))
        cargos = MontoDecimal("0.00")
        abonos = MontoDecimal("0.00")

        for orden, l in enumerate(lineas):
            if "naturaleza" in l and l.get("naturaleza"):
                nat = "cargo" if str(l.get("naturaleza", "")).lower() == "cargo" else "abono"
                imp = redondear_moneda(l.get("importe", 0))
            elif a_decimal(l.get("cargo", 0)) > Decimal("0.00"):
                nat = "cargo"
                imp = redondear_moneda(l.get("cargo", 0))
            else:
                nat = "abono"
                imp = redondear_moneda(l.get("abono", l.get("importe", 0)))

            cta = str(l.get("cuenta", "")).strip()
            desc = str(l.get("descripcion", "")).strip()
            if nat == "cargo":
                cargos = redondear_moneda(cargos + imp)
            else:
                abonos = redondear_moneda(abonos + imp)
            con.execute(
                """INSERT INTO poliza_lineas
                   (poliza_id, orden, cuenta, naturaleza, importe, descripcion, formula_usada)
                   VALUES (?, ?, ?, ?, ?, ?, 'MANUAL')""",
                (poliza_id, orden, cta, nat, imp, desc),
            )

        diferencia = redondear_moneda(cargos - abonos)
        cuadrada = 1 if abs(diferencia) < Decimal("0.01") else 0
        con.execute("UPDATE polizas SET cuadrada = ? WHERE id = ?", (cuadrada, poliza_id))

        max_paso_row = con.execute(
            "SELECT COALESCE(MAX(paso), -1) FROM poliza_auditoria WHERE poliza_id = ?",
            (poliza_id,),
        ).fetchone()
        nuevo_paso = (max_paso_row[0] if max_paso_row else -1) + 1
        estado_txt = "Cuadrada" if cuadrada else f"Descuadrada (Diferencia: ${abs(diferencia):,.2f})"
        con.execute(
            "INSERT INTO poliza_auditoria (poliza_id, paso, detalle) VALUES (?, ?, ?)",
            (
                poliza_id,
                nuevo_paso,
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

def _limpiar_polizas_en_conexion(con, empresa_id):
    con.execute("UPDATE movimientos SET poliza_id = NULL WHERE empresa_id = ? AND poliza_id IS NOT NULL", (empresa_id,))
    con.execute("DELETE FROM polizas WHERE empresa_id = ?", (empresa_id,))

def limpiar_polizas(empresa_id):
    with get_connection() as con:
        _limpiar_polizas_en_conexion(con, empresa_id)

def limpiar_movimientos(empresa_id):
    with get_connection() as con:
        _limpiar_polizas_en_conexion(con, empresa_id)
        con.execute(
            "DELETE FROM cfdi_movimiento WHERE movimiento_id IN (SELECT id FROM movimientos WHERE empresa_id = ?)",
            (empresa_id,),
        )
        con.execute("DELETE FROM movimientos WHERE empresa_id = ?", (empresa_id,))
        con.execute("DELETE FROM documentos_importados WHERE empresa_id = ?", (empresa_id,))
