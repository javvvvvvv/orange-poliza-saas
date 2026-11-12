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

import _rutas  # noqa: F401  (deja backend/app/core y exporters en sys.path)

from rules_repository import RepositorioReglas
from cfdi_asociados import aplicar_impuestos_cfdi, cargar_cfdis_por_movimiento, reconciliar_rfc
from db import get_connection
from formula_engine import redondear_moneda
from licencia import verificar_licencia_empresa
from movimientos_comun import (
    TIPOS_POLIZA,
    TotalPolizasGeneradas,
    _SQL_MOVIMIENTOS_CON_BANCO,
    _cargar_config_fiscal,
    _fila_a_movimiento_dict,
    _generar_para_movimiento,
    _info_descuadre,
    _info_incidencia,
)


def _concepto_y_referencia(fila, cfdis):
    """Concepto y referencia de la póliza: del CFDI si existe, del movimiento si no."""
    referencia = fila["numero_factura"] or ""
    concepto = fila["descripcion"]
    if not cfdis:
        tiene_cfdi = False
        tiene_iva = fila.get("afectable_impuestos") is None or bool(fila.get("afectable_impuestos"))
        from polizas_edicion import formatear_concepto_poliza
        return formatear_concepto_poliza(concepto, tiene_cfdi, tiene_iva), referencia

    if len(cfdis) > 1:
        folios = list(dict.fromkeys((c["folio"] or "").strip() or "U" + c["uuid"][-4:] for c in cfdis if (c["folio"] or "").strip() or c.get("uuid")))
        referencia = ", ".join(folios)[:50]
    else:
        folio = (cfdis[0]["folio"] or "").strip()
        referencia = folio or "UUID: " + cfdis[0]["uuid"][-5:]

    principal = cfdis[0]
    if fila["tipo"] == "egreso":
        concepto = principal["nombre_emisor"] or principal["rfc_emisor"]
    else:
        concepto = principal["nombre_receptor"] or principal["rfc_receptor"]
    
    tiene_cfdi = bool(cfdis)
    tiene_iva = fila.get("afectable_impuestos") is None or bool(fila.get("afectable_impuestos"))
    from polizas_edicion import formatear_concepto_poliza
    return formatear_concepto_poliza(concepto, tiene_cfdi, tiene_iva), referencia


def _ultimo_numero(cur, empresa_id, tipo) -> int:
    cur.execute(
        "SELECT COALESCE(MAX(numero), 0) FROM polizas WHERE empresa_id = ? AND tipo = ?",
        (empresa_id, tipo),
    )
    return int(cur.fetchone()[0])


def generar_polizas_pendientes(empresa_id, inicio_ingreso=None, inicio_egreso=None):
    """Genera pólizas para los movimientos clasificados que aún no tienen póliza.

    El candado transaccional se toma antes de leer los movimientos: así dos
    generaciones simultáneas no pueden crear dos pólizas para el mismo movimiento
    ni repetir un número. Se numera en orden cronológico.
    """
    repo = RepositorioReglas()
    reglas, plantillas = repo.cargar_para_motor(empresa_id)
    nombres_regla = {r.id: r.nombre for r in reglas}

    generadas = 0
    incidencias = []
    with get_connection() as con:
        cur = con.cursor()
        cur.execute("SELECT pg_advisory_xact_lock(hashtext('poliza_num_' || ?::text))", (empresa_id,))

        cfg = _cargar_config_fiscal(con, empresa_id)
        verificar_licencia_empresa(cfg["nombre"])

        movimientos = con.execute(
            _SQL_MOVIMIENTOS_CON_BANCO
            + """ WHERE m.empresa_id = ? AND m.poliza_id IS NULL
                  AND m.estado_clasificacion IN ('automatico', 'manual')
                  ORDER BY m.fecha, m.id""",
            (empresa_id,),
        ).fetchall()
        cfdis_por_movimiento = cargar_cfdis_por_movimiento(con, [f["id"] for f in movimientos])

        ultimos = {
            "ingreso": max(_ultimo_numero(cur, empresa_id, "ingreso"), int(inicio_ingreso or 1) - 1),
            "egreso": max(_ultimo_numero(cur, empresa_id, "egreso"), int(inicio_egreso or 1) - 1),
        }

        for fila in movimientos:
            mov = _fila_a_movimiento_dict(fila)

            if fila["tipo"] not in TIPOS_POLIZA:
                incidencias.append(_info_incidencia(
                    mov, "", f"el tipo de movimiento '{fila['tipo']}' no es ingreso ni egreso", "tipo_invalido"))
                continue

            plantilla = plantillas.get(fila["regla_id"]) if fila["regla_id"] is not None else None
            if not plantilla:
                incidencias.append(_info_incidencia(
                    mov, "", "no tiene regla asignada, o la regla está inactiva o sin plantilla", "sin_plantilla"))
                continue
            nombre_regla = nombres_regla.get(fila["regla_id"], f"Regla #{fila['regla_id']}")

            cfdis = cfdis_por_movimiento.get(fila["id"], [])
            conflicto = reconciliar_rfc(mov, cfdis)
            if conflicto:
                incidencias.append(_info_incidencia(mov, nombre_regla, conflicto, "conflicto_rfc"))
                continue
            aplicar_impuestos_cfdi(mov, cfdis)

            concepto, referencia = _concepto_y_referencia(fila, cfdis)
            mov["descripcion"] = concepto
            mov["numero_factura"] = referencia

            resultado = _generar_para_movimiento(
                mov, plantilla, nombre_regla, "Regla asignada al clasificar el movimiento.", cfg)
            if not resultado.cuadrada:
                incidencias.append(_info_descuadre(mov, nombre_regla, resultado))
                continue

            ultimos[fila["tipo"]] += 1
            numero = ultimos[fila["tipo"]]

            # Determinar si afecta IVA fiscal
            tiene_linea_iva = False
            cuentas_iva = {
                str(cfg.get(k) or "").replace("-", "").replace(" ", "").strip()
                for k in ["cuenta_iva_acreditable", "cuenta_iva_por_acreditar", "cuenta_iva_trasladado", "cuenta_iva_por_trasladar"]
            }
            cuentas_iva.discard("")
            for l in resultado.lineas:
                if str(l.cuenta).replace("-", "").replace(" ", "").strip() in cuentas_iva and l.importe > 0:
                    tiene_linea_iva = True
                    break

            afectable_mov = fila.get("afectable_impuestos")
            if afectable_mov is not None and not bool(afectable_mov):
                afectar_fiscal = 0
            elif bool(cfdis):
                afectar_fiscal = 1
            elif tiene_linea_iva:
                afectar_fiscal = 1
            else:
                afectar_fiscal = 0

            cur.execute(
                """INSERT INTO polizas (empresa_id, tipo, numero, fecha, referencia, concepto, cuadrada, enviada_contpaqi, afectar_iva_fiscal)
                   VALUES (?, ?, ?, ?, ?, ?, 1, 0, ?)""",
                (empresa_id, fila["tipo"], numero, fila["fecha"], referencia, concepto, afectar_fiscal),
            )
            poliza_id = cur.lastrowid

            for orden, linea in enumerate(resultado.lineas):
                cur.execute(
                    """INSERT INTO poliza_lineas
                       (poliza_id, orden, cuenta, naturaleza, importe, descripcion, formula_usada)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (poliza_id, orden, linea.cuenta, linea.naturaleza,
                     redondear_moneda(linea.importe), linea.descripcion, linea.formula_usada),
                )
            for paso, texto in enumerate(resultado.explicacion):
                cur.execute(
                    "INSERT INTO poliza_auditoria (poliza_id, paso, detalle) VALUES (?, ?, ?)",
                    (poliza_id, paso, texto),
                )

            cur.execute(
                "UPDATE movimientos SET poliza_id = ? WHERE id = ? AND empresa_id = ?",
                (poliza_id, fila["id"], empresa_id),
            )
            generadas += 1

    return TotalPolizasGeneradas(generadas, descuadres=incidencias)
