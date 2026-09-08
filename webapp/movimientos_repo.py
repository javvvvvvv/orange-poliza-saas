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
import sys
import os
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "app", "core"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "app", "exporters"))

from rule_engine import encontrar_regla, normalizar_texto
from learning_engine import sugerir_regla_desde_clasificacion_manual
from policy_generator import generar_poliza
from rules_repository import RepositorioReglas
from contpaqi_exporter import MovimientoPoliza, ImpuestoPoliza, FacturaAplicada
from contpaqi_txt_exporter import exportar_polizas_contpaqi_txt
from licencia import verificar_licencia_empresa, LicenciaInvalida

from db import get_connection


def insertar_movimientos(empresa_id, documento_id, movimientos):
    con = get_connection()
    cur = con.cursor()
    ids = []
    for mov in movimientos:
        cur.execute(
            """INSERT INTO movimientos
               (empresa_id, documento_id, fecha, descripcion, descripcion_normalizada,
                tipo, total, rfc_contraparte, cuenta_bancaria_contraparte,
                referencia_bancaria, numero_factura, fila_original, estado_clasificacion)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pendiente')""",
            (
                empresa_id, documento_id, mov["fecha"].strftime("%Y-%m-%d"),
                mov["descripcion"], normalizar_texto(mov["descripcion"]),
                mov["tipo"], mov["total"], mov.get("rfc_contraparte"),
                mov.get("cuenta_bancaria_contraparte"), mov.get("referencia_bancaria"),
                mov.get("numero_factura") or "", mov.get("fila_original"),
            ),
        )
        ids.append(cur.lastrowid)
    con.commit()
    con.close()
    return ids



import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend", "app", "core")))
from ml_engine import EntrenadorML

def registrar_memoria_ml_exitosa(empresa_id, descripcion, tipo, regla_id):
    con = get_connection()
    try:
        con.execute(
            """INSERT INTO memoria_ml (empresa_id, descripcion_limpia, tipo, regla_asignada_id) 
               VALUES (?, ?, ?, ?)
               ON CONFLICT(empresa_id, descripcion_limpia, tipo, regla_asignada_id) 
               DO UPDATE SET frecuencia = memoria_ml.frecuencia + 1, ultima_vez = CURRENT_TIMESTAMP""",
            (empresa_id, str(descripcion).strip(), str(tipo).strip(), regla_id)
        )
        con.commit()
    finally:
        con.close()

def entrenar_modelo_empresa(empresa_id):
    con = get_connection()
    datos = con.execute("SELECT descripcion_limpia as descripcion, tipo, regla_asignada_id as regla_id FROM memoria_ml WHERE empresa_id = ?", (empresa_id,)).fetchall()
    con.close()
    if datos:
        EntrenadorML.entrenar(empresa_id, [dict(d) for d in datos])

def _fila_a_movimiento_dict(fila):
    return {
        "id": fila["id"],
        "empresa_id": fila["empresa_id"],
        "fecha": datetime.strptime(fila["fecha"], "%Y-%m-%d"),
        "descripcion": fila["descripcion"],
        "tipo": fila["tipo"],
        "total": fila["total"],
        "rfc_contraparte": fila["rfc_contraparte"],
        "cuenta_bancaria_contraparte": fila["cuenta_bancaria_contraparte"],
        "numero_factura": fila["numero_factura"],
        "cuenta_banco": fila.get("cuenta_banco"),
        "tiene_iva": True,
        "ret_iva": 0.0,
        "ret_isr": 0.0,
        "tipo_cambio": 1.0,
    }


def clasificar_pendientes(empresa_id):

    con = get_connection()
    emp_cfg = con.execute("SELECT tasa_iva, tasa_retencion_iva, tasa_retencion_isr FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
    if emp_cfg:
        tasa_iva = emp_cfg["tasa_iva"]
        tasa_ret_iva = emp_cfg["tasa_retencion_iva"]
        tasa_ret_isr = emp_cfg["tasa_retencion_isr"]
    else:
        tasa_ret_iva = 0.0
        tasa_ret_isr = 0.0

    """Corre el motor de reglas sobre los movimientos pendientes de la
    empresa y actualiza su estado. Regresa cuÃ¡ntos quedaron
    automÃ¡ticos y cuÃ¡ntos siguen pendientes."""
    repo = RepositorioReglas()
    reglas, plantillas = repo.cargar_para_motor(empresa_id)

    con = get_connection()
    pendientes = con.execute(
        "SELECT * FROM movimientos WHERE empresa_id = ? AND estado_clasificacion = 'pendiente'",
        (empresa_id,),
    ).fetchall()

    automaticos = 0
    usos_por_regla = {}
    for fila in pendientes:
        mov = _fila_a_movimiento_dict(fila)
        match = encontrar_regla(mov, reglas)
        if match.regla is None:
            continue
            
        cur = con.cursor()
        cur.execute("""
            SELECT c.total AS cfdi_total, cm.importe_aplicado,
                   COALESCE((SELECT SUM(importe) FROM cfdi_impuestos ci WHERE ci.cfdi_id = c.id AND ci.es_retencion = 0 AND ci.tipo_impuesto = 'IVA'), 0) as iva_cfdi
            FROM cfdis c
            JOIN cfdi_movimiento cm ON cm.cfdi_id = c.id
            WHERE cm.movimiento_id = ? AND cm.confirmado = 1
        """, (fila["id"],))
        cfdis_asoc = cur.fetchall()
        
        if cfdis_asoc:
            iva_exacto_tot = 0.0
            for c_asoc in cfdis_asoc:
                if c_asoc["cfdi_total"] > 0:
                    prop = min(1.0, float(c_asoc["importe_aplicado"]) / float(c_asoc["cfdi_total"]))
                    iva_exacto_tot += float(c_asoc["iva_cfdi"]) * prop
            mov["iva_exacto"] = round(iva_exacto_tot, 2)
            mov["tiene_iva"] = True if iva_exacto_tot > 0 else False

        resultado = generar_poliza(
            movimiento=mov, plantilla=plantillas[match.regla.id],
            nombre_regla=match.regla.nombre, motivo_match=match.motivo,
            tasa_iva=tasa_iva, tasa_ret_iva=tasa_ret_iva, tasa_ret_isr=tasa_ret_isr
        )
        if not resultado.cuadrada:
            continue

        con.execute(
            """UPDATE movimientos
               SET regla_id = ?, confianza_clasificacion = ?, estado_clasificacion = 'automatico'
               WHERE id = ?""",
            (match.regla.id, match.confianza, fila["id"]),
        )
        usos_por_regla[match.regla.id] = usos_por_regla.get(match.regla.id, 0) + 1
        automaticos += 1
        registrar_memoria_ml_exitosa(empresa_id, mov["descripcion"], mov["tipo"], match.regla.id)

    con.commit()
    con.close()

    for regla_id, veces in usos_por_regla.items():
        for _ in range(veces):
            repo.registrar_uso(regla_id)

    return {"automaticos": automaticos, "pendientes_restantes": len(pendientes) - automaticos}


def listar_movimientos(empresa_id, estado=None):
    con = get_connection()
    query = "SELECT * FROM movimientos WHERE empresa_id = ?"
    params = [empresa_id]
    if estado:
        query += " AND estado_clasificacion = ?"
        params.append(estado)
    query += " ORDER BY fecha DESC, id DESC"
    filas = con.execute(query, params).fetchall()
    con.close()
    return filas


def sugerencia_para_movimiento(movimiento_id):
    con = get_connection()
    fila = con.execute("SELECT * FROM movimientos WHERE id = ?", (movimiento_id,)).fetchone()
    con.close()
    if fila is None:
        return None, None
    mov = _fila_a_movimiento_dict(fila)
    return mov, sugerir_regla_desde_clasificacion_manual(mov)



def sugerencia_para_movimiento(movimiento_id):
    con = get_connection()
    fila = con.execute("SELECT * FROM movimientos WHERE id = ?", (movimiento_id,)).fetchone()
    con.close()
    if not fila:
        return None, None

    import sys
    import os
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend", "app", "core")))
    from learning_engine import sugerir_regla_desde_clasificacion_manual

    mov = _fila_a_movimiento_dict(fila)
    con = get_connection()
    empresa = con.execute("SELECT rfc FROM empresas WHERE id = ?", (fila["empresa_id"],)).fetchone()
    con.close()
    rfc_empresa = empresa["rfc"] if empresa else ""

    return mov, sugerir_regla_desde_clasificacion_manual(mov, rfc_empresa=rfc_empresa)

def generar_polizas_pendientes(empresa_id, inicio_ingreso=None, inicio_egreso=None):

    con = get_connection()
    emp_cfg = con.execute("SELECT tasa_iva, tasa_retencion_iva, tasa_retencion_isr FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
    if emp_cfg:
        tasa_iva = emp_cfg["tasa_iva"]
        tasa_ret_iva = emp_cfg["tasa_retencion_iva"]
        tasa_ret_isr = emp_cfg["tasa_retencion_isr"]
    else:
        tasa_ret_iva = 0.0
        tasa_ret_isr = 0.0

    """Genera pÃ³lizas para todos los movimientos ya clasificados
    (estado 'automatico' o 'manual') que todavÃ­a no tienen pÃ³liza."""
    con_lic = get_connection()
    empresa_lic = con_lic.execute("SELECT nombre FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
    con_lic.close()
    verificar_licencia_empresa(empresa_lic["nombre"] if empresa_lic else "")

    repo = RepositorioReglas()
    _, plantillas = repo.cargar_para_motor(empresa_id)

    con = get_connection()
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
    
    if inicio_ingreso is not None and int(inicio_ingreso) > 0 and ultimo_ingreso < int(inicio_ingreso):
        ultimo_ingreso = int(inicio_ingreso) - 1
    if inicio_egreso is not None and int(inicio_egreso) > 0 and ultimo_egreso < int(inicio_egreso):
        ultimo_egreso = int(inicio_egreso) - 1

    generadas = 0
    cur = con.cursor()

    for fila in movimientos:
        if fila["regla_id"] is None:
            continue
        plantilla = plantillas.get(fila["regla_id"])
        if not plantilla:
            continue

        mov = _fila_a_movimiento_dict(fila)
        cur.execute("SELECT nombre FROM reglas WHERE id = ?", (fila["regla_id"],))
        nombre_regla = cur.fetchone()["nombre"]

        # Buscar CFDIs asociados al movimiento ANTES de generar la poliza
        cur.execute("""
            SELECT c.folio, c.uuid, c.nombre_emisor, c.nombre_receptor, c.rfc_emisor, c.rfc_receptor, 
                   c.total AS cfdi_total, cm.importe_aplicado,
                   COALESCE((SELECT SUM(importe) FROM cfdi_impuestos ci WHERE ci.cfdi_id = c.id AND ci.es_retencion = 0 AND ci.tipo_impuesto = 'IVA'), 0) as iva_cfdi
            FROM cfdis c
            JOIN cfdi_movimiento cm ON cm.cfdi_id = c.id
            WHERE cm.movimiento_id = ? AND cm.confirmado = 1
            ORDER BY cm.importe_aplicado DESC
        """, (fila["id"],))
        cfdis_asociados = cur.fetchall()
        
        # Calcular IVA exacto si hay CFDIs
        if cfdis_asociados:
            iva_exacto_total = 0.0
            for c_asoc in cfdis_asociados:
                if c_asoc["cfdi_total"] > 0:
                    proporcion = min(1.0, float(c_asoc["importe_aplicado"]) / float(c_asoc["cfdi_total"]))
                    iva_exacto_total += float(c_asoc["iva_cfdi"]) * proporcion
            mov["iva_exacto"] = round(iva_exacto_total, 2)
            mov["tiene_iva"] = True if iva_exacto_total > 0 else False


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
                p_referencia = ", ".join(folios)[:50] # Contpaqi reference limit
            else:
                cfdi_asoc = cfdis_asociados[0]
                if cfdi_asoc["folio"] and cfdi_asoc["folio"].strip():
                    p_referencia = cfdi_asoc["folio"].strip()
                else:
                    p_referencia = "UUID: " + cfdi_asoc["uuid"][-5:]
            
            # El concepto lo tomamos del proveedor principal (el de mayor importe)
            cfdi_principal = cfdis_asociados[0]
            if fila["tipo"] == "egreso":
                p_concepto = cfdi_principal["nombre_emisor"] or cfdi_principal["rfc_emisor"]
            else:
                p_concepto = cfdi_principal["nombre_receptor"] or cfdi_principal["rfc_receptor"]

        # Intervenir el movimiento para que la poliza y sus lineas asuman el nuevo concepto
        mov["descripcion"] = p_concepto
        mov["numero_factura"] = p_referencia

        resultado = generar_poliza(
            movimiento=mov, plantilla=plantilla, nombre_regla=nombre_regla,
            motivo_match="Reclasificacion al generar poliza.", tasa_iva=tasa_iva, tasa_ret_iva=tasa_ret_iva, tasa_ret_isr=tasa_ret_isr
        )
        if not resultado.cuadrada:
            continue

        if fila["tipo"] == "ingreso":
            ultimo_ingreso += 1
            numero = ultimo_ingreso
        else:
            ultimo_egreso += 1
            numero = ultimo_egreso
        
        cur.execute(
            """INSERT INTO polizas (empresa_id, tipo, numero, fecha, referencia, concepto, cuadrada)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (empresa_id, fila["tipo"], numero, fila["fecha"], p_referencia, p_concepto),
        )
        poliza_id = cur.lastrowid

        for orden, linea in enumerate(resultado.lineas):
            cur.execute(
                """INSERT INTO poliza_lineas
                   (poliza_id, orden, cuenta, naturaleza, importe, descripcion, formula_usada)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (poliza_id, orden, linea.cuenta, linea.naturaleza, linea.importe,
                 linea.descripcion, linea.formula_usada),
            )

        for paso, texto in enumerate(resultado.explicacion):
            cur.execute(
                "INSERT INTO poliza_auditoria (poliza_id, paso, detalle) VALUES (?, ?, ?)",
                (poliza_id, paso, texto),
            )

        cur.execute("UPDATE movimientos SET poliza_id = ? WHERE id = ?", (poliza_id, fila["id"]))
        generadas += 1

    con.commit()
    con.close()
    return generadas


def exportar_polizas(empresa_id, ruta_salida):
    con = get_connection()

    empresa = con.execute("SELECT * FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
    try:
        verificar_licencia_empresa(empresa["nombre"] if empresa else "")
    except LicenciaInvalida:
        con.close()
        raise

    polizas = con.execute(
        "SELECT * FROM polizas WHERE empresa_id = ? ORDER BY tipo, numero", (empresa_id,)
    ).fetchall()

    movimientos_poliza = []
    
    # Eager Loading para N+1
    map_lineas = {}
    map_movs = {}
    if polizas:
        p_ids = [str(p["id"]) for p in polizas]
        placeholders = ",".join(p_ids)
        
        todas_lineas = con.execute(f"SELECT * FROM poliza_lineas WHERE poliza_id IN ({placeholders}) ORDER BY poliza_id, orden").fetchall()
        for l in todas_lineas:
            pid = l["poliza_id"]
            if pid not in map_lineas: map_lineas[pid] = []
            map_lineas[pid].append(l)
            
        todos_movs = con.execute(f"""SELECT m.*, b.cuenta_contable AS cuenta_banco
           FROM movimientos m
           LEFT JOIN documentos_importados d ON d.id = m.documento_id
           LEFT JOIN bancos b ON b.id = d.banco_id
           WHERE m.poliza_id IN ({placeholders})""").fetchall()
        for m in todos_movs:
            map_movs[m["poliza_id"]] = m

    for p in polizas:
        lineas_db = map_lineas.get(p["id"], [])
        mov_db = map_movs.get(p["id"])

        class _Linea:
            def __init__(self, fila):
                self.cuenta = fila["cuenta"]
                self.naturaleza = fila["naturaleza"]
                self.importe = fila["importe"]
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
            # Un movimiento puede quedar ligado a varias facturas (pago
            # combinado, referencia "VARIOS" en Contpaqi): cada una se
            # exporta como su propia FacturaAplicada, con sus propios
            # impuestos prorrateados si el importe aplicado es parcial
            # del total de esa factura (pago parcial/abono).
            cuenta_banco_mov = cuenta_banco_real.replace("-", "")

            for cf in cfdi_filas:
                # Si el CFDI es de Pago (REP) y tiene un UUID relacionado de la factura origen,
                # usamos el UUID de la factura para la poliza y el F4. Si trae varios, tomamos el primero.
                uuid_final = cf["uuid"]
                if cf.get("uuids_relacionados"):
                    uuids_lista = cf["uuids_relacionados"].split(",")
                    if uuids_lista and uuids_lista[0].strip():
                        uuid_final = uuids_lista[0].strip()
                
                factor = (cf["importe_aplicado"] / cf["cfdi_total"]) if cf["cfdi_total"] else 1.0
                impuestos_db = con.execute(
                    "SELECT * FROM cfdi_impuestos WHERE cfdi_id = ?", (cf["cfdi_id"],)
                ).fetchall()
                impuestos_factura = [
                    ImpuestoPoliza(
                        base=round(i["base"] * factor, 2),
                        importe=round(i["importe"] * factor, 2),
                        tasa=i["tasa"],
                        es_retencion=bool(i["es_retencion"]),
                        tipo_impuesto=i["tipo_impuesto"] or "IVA",
                    )
                    for i in impuestos_db
                ]
                # RFC de la contraparte de ESTA factura: si el CFDI es
                # 'recibido' (egreso), la contraparte es el emisor; si es
                # 'emitido' (ingreso), es el receptor.
                rfc_persona = (
                    cf["rfc_emisor"] if cf["cfdi_tipo"] == "recibido" else cf["rfc_receptor"]
                ) or ""
                facturas.append(FacturaAplicada(
                    uuid=uuid_final, serie=cf["serie"] or "", folio=cf["folio"] or "",
                    rfc_persona=rfc_persona, impuestos=impuestos_factura,
                ))
                cfdi_impuestos.extend(impuestos_factura)

            # Compatibilidad hacia atrÃ¡s (un solo CFDI): se llenan tambiÃ©n
            # los campos singulares por si algo mÃ¡s los sigue leyendo.
            if len(facturas) == 1:
                cfdi_uuid, cfdi_serie, cfdi_folio = facturas[0].uuid, facturas[0].serie, facturas[0].folio
        elif mov_db and (mov_db["afectable_impuestos"] is None or mov_db["afectable_impuestos"]):
            # Sin CFDI conciliado, pero el movimiento SÃ afecta impuestos
            # (se clasificÃ³ a mano): igual debe llevar F4/F6. Si se marcÃ³
            # "aplica IVA" al clasificarlo, la lÃ­nea de IVA que ya generÃ³
            # policy_generator (contra la cuenta de IVA acreditable/
            # trasladado configurada) nos dice cuÃ¡nto es; si no se marcÃ³,
            # se llena en exento (base = total, IVA = 0, tasa = 0).
            #
            # Las RETENCIONES NO tienen mÃ©todo alterno: solo existen si
            # vienen del CFDI (<Retenciones> del XML). Sin CFDI conciliado
            # no se calcula ni se estima ninguna retenciÃ³n aquÃ­ â€” decisiÃ³n
            # explÃ­cita del usuario, no un pendiente.
            cuenta_banco_mov = cuenta_banco_real.replace("-", "")
            cuenta_iva_esperada = (
                empresa["cuenta_iva_acreditable"] if p["tipo"] == "egreso"
                else empresa["cuenta_iva_trasladado"]
            ) if empresa else None
            cuenta_iva_esperada = str(cuenta_iva_esperada or "").replace("-", "")

            linea_iva = next(
                (l for l in lineas if str(l.cuenta).replace("-", "") == cuenta_iva_esperada
                 and cuenta_iva_esperada), None
            )
            total_mov = mov_db["total"]
            if linea_iva:
                importe_iva = round(linea_iva.importe, 2)
                cfdi_impuestos.append(ImpuestoPoliza(
                    base=round(total_mov - importe_iva, 2),
                    importe=importe_iva,
                    tasa=empresa["tasa_iva"] if empresa else 0.16,
                ))
            else:
                cfdi_impuestos.append(ImpuestoPoliza(base=round(total_mov, 2), importe=0.0, tasa=0.0))

        movimientos_poliza.append(MovimientoPoliza(
            numero_poliza=p["numero"], tipo=p["tipo"],
            fecha=datetime.strptime(p["fecha"], "%Y-%m-%d"),
            descripcion=p.get("concepto") or (mov_db["descripcion"] if mov_db else ""),
            lineas=lineas, tiene_iva=bool(cfdi_uuid or facturas),
            numero_factura=p["referencia"] or "",
            cfdi_uuid=cfdi_uuid, cfdi_serie=cfdi_serie, cfdi_folio=cfdi_folio,
            cfdi_impuestos=cfdi_impuestos, facturas=facturas, cuenta_banco=cuenta_banco_mov,
        ))

    con.close()
    # Salida en .txt (layout de importaciÃ³n de Contpaqi), ya no .xls: el
    # archivo de Excel salÃ­a del proceso de conversiÃ³n con LibreOffice,
    # que ya no se usa para esta exportaciÃ³n.
    return exportar_polizas_contpaqi_txt(movimientos_poliza, ruta_salida,
                                          nombre_empresa=empresa["nombre"] if empresa else None)


def limpiar_polizas(empresa_id):
    from db import get_connection
    con = get_connection()
    try:
        # Get poliza ids
        polizas = con.execute("SELECT id FROM polizas WHERE empresa_id = ?", (empresa_id,)).fetchall()
        p_ids = [p["id"] for p in polizas]
        if p_ids:
            # PostgreSQL doesn't support '?' with IN clause easily without formatting if we use standard DB-API, but we use translated placeholders in db.py.
            # Let's just do it manually
            for p_id in p_ids:
                con.execute("DELETE FROM poliza_lineas WHERE poliza_id = ?", (p_id,))
                con.execute("DELETE FROM poliza_auditoria WHERE poliza_id = ?", (p_id,))
                con.execute("UPDATE movimientos SET poliza_id = NULL WHERE poliza_id = ?", (p_id,))
            con.execute("DELETE FROM polizas WHERE empresa_id = ?", (empresa_id,))
            con.commit()
    finally:
        con.close()

def limpiar_movimientos(empresa_id):
    from db import get_connection
    con = get_connection()
    try:
        # Limpiar todo
        limpiar_polizas(empresa_id)
        
        # Eliminar movimientos
        con.execute("DELETE FROM cfdi_movimiento WHERE movimiento_id IN (SELECT id FROM movimientos WHERE empresa_id = ?)", (empresa_id,))
        con.execute("DELETE FROM movimientos WHERE empresa_id = ?", (empresa_id,))
        
        # Opcional: Eliminar documentos importados si pertenecen a esta empresa (wait, no empresa_id in documentos_importados? Yes there is in importacion_repo. But usually we don't delete documentos_importados unless it has empresa_id).
        con.execute("DELETE FROM documentos_importados WHERE empresa_id = ?", (empresa_id,))
        con.commit()
    finally:
        con.close()

