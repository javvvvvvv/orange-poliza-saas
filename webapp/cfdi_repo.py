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
import uuid
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "app", "cfdi"))

from cfdi_importer import importar_cfdi, ErrorCFDI
from conciliador_avanzado import conciliar_avanzado

from db import get_connection

UMBRAL_AUTOCONFIRMAR = 95  # solo 'exacto' (100) se aplica solo; el resto se revisa


def importar_y_conciliar(empresa_id, rutas_xml, rfc_empresa, ventana_dias=45, tipo_hint=None):
    """
    :param tipo_hint: 'emitido' | 'recibido' | None. Si el usuario ya
        indicÃ³ desde quÃ© botÃ³n subiÃ³ el archivo (Emitidos/Recibidos),
        se usa directo en vez de adivinar por el RFC de la empresa â€”
        mÃ¡s confiable si el RFC de la empresa no estÃ¡ bien capturado en
        ConfiguraciÃ³n todavÃ­a.
    """
    con = get_connection()
    cur = con.cursor()

    importados = 0
    errores = []

    for ruta in rutas_xml:
        try:
            cfdi = importar_cfdi(ruta)
        except ErrorCFDI as e:
            errores.append({"archivo": os.path.basename(ruta), "error": str(e)})
            continue

        existe = cur.execute(
            "SELECT id FROM cfdis WHERE empresa_id = ? AND uuid = ?", (empresa_id, cfdi.uuid)
        ).fetchone()
        if existe:
            errores.append({"archivo": os.path.basename(ruta), "error": "CFDI ya importado (UUID duplicado)."})
            continue

        if tipo_hint in ("emitido", "recibido"):
            tipo = tipo_hint
        else:
            tipo = "recibido" if cfdi.rfc_receptor == rfc_empresa.strip().upper() else (
                "emitido" if cfdi.rfc_emisor == rfc_empresa.strip().upper() else "desconocido"
            )
        cur.execute(
            """INSERT INTO cfdis
               (empresa_id, uuid, tipo, rfc_emisor, nombre_emisor, rfc_receptor, nombre_receptor, fecha, total,
                subtotal, serie, folio, archivo_origen)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (empresa_id, cfdi.uuid, tipo, cfdi.rfc_emisor, cfdi.nombre_emisor, cfdi.rfc_receptor, cfdi.nombre_receptor,
             cfdi.fecha.strftime("%Y-%m-%d"), cfdi.total, cfdi.subtotal,
             cfdi.serie, cfdi.folio, ruta),
        )
        cfdi_id = cur.lastrowid
        importados += 1

        for imp in cfdi.impuestos_trasladados:
            cur.execute(
                "INSERT INTO cfdi_impuestos (cfdi_id, base, importe, tasa, es_retencion, tipo_impuesto) "
                "VALUES (?, ?, ?, ?, 0, 'IVA')",
                (cfdi_id, imp.base, imp.importe, imp.tasa),
            )
        # Las retenciones del CFDI (<Retenciones>) no traen Base ni
        # TasaOCuota en el XML â€” se usa el SubTotal del comprobante como
        # base (asÃ­ viene en pÃ³lizas reales con retenciones) y se
        # calcula la tasa a partir de eso, para poder llenar F4 igual
        # que una lÃ­nea de traslado.
        catalogo_impuesto_sat = {"001": "ISR", "002": "IVA", "003": "IEPS"}
        for ret in cfdi.impuestos_retenidos:
            tipo = catalogo_impuesto_sat.get(ret.impuesto, "IVA")
            base = cfdi.subtotal
            tasa = round(ret.importe / base, 6) if base else 0.0
            cur.execute(
                "INSERT INTO cfdi_impuestos (cfdi_id, base, importe, tasa, es_retencion, tipo_impuesto) "
                "VALUES (?, ?, ?, ?, 1, ?)",
                (cfdi_id, base, ret.importe, tasa, tipo),
            )

    con.commit()

    # -----------------------------------------------------------------
    # Conciliar TODOS los CFDI de la empresa que aÃºn no tienen ninguna
    # fila en cfdi_movimiento contra TODOS los movimientos que aÃºn no
    # estÃ¡n conciliados. AsÃ­, si subes CFDIs antes o despuÃ©s del Excel,
    # igual se cruzan.
    # -----------------------------------------------------------------
    cfdis_db = cur.execute(
        """SELECT c.* FROM cfdis c
           WHERE c.empresa_id = ? AND c.id NOT IN (SELECT cfdi_id FROM cfdi_movimiento)""",
        (empresa_id,),
    ).fetchall()

    movimientos_db = cur.execute(
        """SELECT m.* FROM movimientos m
           WHERE m.empresa_id = ? AND m.id NOT IN (SELECT movimiento_id FROM cfdi_movimiento)""",
        (empresa_id,),
    ).fetchall()

    cfdis_obj = []
    mapa_cfdi_id = {}
    for fila in cfdis_db:
        impuestos = cur.execute(
            "SELECT * FROM cfdi_impuestos WHERE cfdi_id = ?", (fila["id"],)
        ).fetchall()
        from cfdi_importer import CFDI, ImpuestoTraslado
        obj = CFDI(
            uuid=fila["uuid"], rfc_emisor=fila["rfc_emisor"], nombre_emisor="",
            rfc_receptor=fila["rfc_receptor"], nombre_receptor="",
            fecha=datetime.strptime(fila["fecha"], "%Y-%m-%d"), total=fila["total"],
            subtotal=fila["subtotal"], serie=fila["serie"], folio=fila["folio"],
            tipo_comprobante="I",
            impuestos_trasladados=[ImpuestoTraslado(i["base"], i["importe"], i["tasa"]) for i in impuestos],
        )
        cfdis_obj.append(obj)
        mapa_cfdi_id[id(obj)] = fila["id"]

    movs_dicts = []
    mapa_mov_id = {}
    for fila in movimientos_db:
        d = {
            "tipo": fila["tipo"], "total": fila["total"],
            "fecha": datetime.strptime(fila["fecha"], "%Y-%m-%d"),
            "rfc_contraparte": fila["rfc_contraparte"], "descripcion": fila["descripcion"],
            "referencia": fila["referencia_bancaria"] or "",
            "numero_factura": fila["numero_factura"] or "",
        }
        movs_dicts.append(d)
        mapa_mov_id[id(d)] = fila["id"]

    tipo_por_uuid = {fila["uuid"]: fila["tipo"] for fila in cfdis_db}
    propuestas = conciliar_avanzado(
        cfdis_obj, movs_dicts, rfc_empresa, ventana_dias=ventana_dias, tipo_por_uuid=tipo_por_uuid,
    )

    conciliados_resumen = []
    for propuesta in propuestas:
        grupo_id = uuid.uuid4().hex
        auto_confirmar = True # Autoconfirmamos todas las sugerencias avanzadas (N:1, 1:N, parciales) por peticion del usuario

        for aplicacion in propuesta.aplicaciones:
            cfdi_id = mapa_cfdi_id[id(aplicacion.cfdi)]
            movimiento_id = mapa_mov_id[id(aplicacion.movimiento)]
            cur.execute(
                """INSERT INTO cfdi_movimiento
                   (grupo_id, cfdi_id, movimiento_id, importe_aplicado, tipo_match,
                    confianza, motivo, confirmado)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (grupo_id, cfdi_id, movimiento_id, aplicacion.importe_aplicado,
                 propuesta.tipo_match, propuesta.confianza, propuesta.motivo,
                 1 if auto_confirmar else 0),
            )
            if auto_confirmar:
                folio = f"{aplicacion.cfdi.serie or ''}{aplicacion.cfdi.folio or ''}".strip() or aplicacion.cfdi.uuid[:8]
                rfc_contraparte = (
                    aplicacion.cfdi.rfc_emisor if aplicacion.cfdi.rfc_receptor == rfc_empresa.strip().upper()
                    else aplicacion.cfdi.rfc_receptor
                )
                cur.execute(
                    """UPDATE movimientos SET numero_factura = ?,
                       rfc_contraparte = COALESCE(NULLIF(rfc_contraparte, ''), ?)
                       WHERE id = ?""",
                    (folio, rfc_contraparte, movimiento_id),
                )

        if auto_confirmar:
            conciliados_resumen.append({
                "tipo_match": propuesta.tipo_match, "confianza": propuesta.confianza,
                "motivo": propuesta.motivo,
            })

    con.commit()
    con.close()

    pendientes_revision = sum(1 for p in propuestas if not (p.tipo_match == "exacto" and p.confianza >= UMBRAL_AUTOCONFIRMAR))

    return {
        "importados": importados, "errores": errores,
        "conciliados": conciliados_resumen,
        "pendientes_revision": pendientes_revision,
    }


def listar_propuestas_pendientes(empresa_id):
    con = get_connection()
    filas = con.execute(
        """SELECT cm.grupo_id, cm.tipo_match, cm.confianza, cm.motivo,
                  STRING_AGG(DISTINCT c.serie || c.folio, ', ') AS folios,
                  STRING_AGG(DISTINCT m.descripcion, ', ') AS descripciones,
                  SUM(cm.importe_aplicado) AS total_aplicado,
                  COUNT(DISTINCT cm.cfdi_id) AS num_cfdis,
                  COUNT(DISTINCT cm.movimiento_id) AS num_movimientos
           FROM cfdi_movimiento cm
           JOIN cfdis c ON c.id = cm.cfdi_id
           JOIN movimientos m ON m.id = cm.movimiento_id
           WHERE m.empresa_id = ? AND cm.confirmado = 0
           GROUP BY cm.grupo_id, cm.tipo_match, cm.confianza, cm.motivo
           ORDER BY cm.confianza DESC""",
        (empresa_id,),
    ).fetchall()
    con.close()
    return filas


def confirmar_propuesta(grupo_id):
    con = get_connection()
    cur = con.cursor()
    filas = cur.execute(
        """SELECT cm.*, c.serie, c.folio, c.uuid, c.rfc_emisor, c.rfc_receptor
           FROM cfdi_movimiento cm
           JOIN cfdis c ON c.id = cm.cfdi_id WHERE cm.grupo_id = ?""",
        (grupo_id,),
    ).fetchall()

    for fila in filas:
        cur.execute("UPDATE cfdi_movimiento SET confirmado = 1 WHERE id = ?", (fila["id"],))
        folio = f"{fila['serie'] or ''}{fila['folio'] or ''}".strip() or fila["uuid"][:8]
        mov_actual = cur.execute(
            "SELECT numero_factura, rfc_contraparte FROM movimientos WHERE id = ?", (fila["movimiento_id"],)
        ).fetchone()
        factura_actual = (mov_actual["numero_factura"] or "").strip()
        nuevo_valor = f"{factura_actual}+{folio}" if factura_actual and folio not in factura_actual else (folio if not factura_actual else factura_actual)
        cur.execute(
            "UPDATE movimientos SET numero_factura = ? WHERE id = ?",
            (nuevo_valor, fila["movimiento_id"]),
        )
        if not (mov_actual["rfc_contraparte"] or "").strip():
            # No sabemos con certeza si nosotros somos emisor o receptor
            # aquÃ­ (podrÃ­a ser cualquiera); dejamos que rfc_contraparte
            # se complete solo cuando el tipo de match ya lo determinÃ³
            # (esto se resuelve mejor en el flujo de auto-confirmaciÃ³n;
            # aquÃ­ solo evitamos sobreescribir con un valor incorrecto).
            pass

    con.commit()
    con.close()


def rechazar_propuesta(grupo_id):
    con = get_connection()
    con.execute("DELETE FROM cfdi_movimiento WHERE grupo_id = ?", (grupo_id,))
    con.commit()
    con.close()

