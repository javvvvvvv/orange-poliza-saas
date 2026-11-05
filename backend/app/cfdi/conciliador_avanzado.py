# -*- coding: utf-8 -*-
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
"""Conciliador avanzado: resuelve pagos parciales, N facturas -> 1
movimiento, N movimientos -> 1 factura, y coincidencia por monto sin
RFC. `cfdi_matcher.py` cubre solo el caso 1:1 simple."""

import base64
import itertools
import json
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from cfdi_importer import CFDI
from cfdi_matcher import clasificar_cfdi, _dentro_de_ventana

_DIR = os.path.dirname(__file__)
_LLAVE = b"OC-4c-cfdi-2026"

def _cargar_mapa() -> dict:
    with open(os.path.join(_DIR, "_c4b1.dat")) as f:
        blob = f.read().strip()
    xored = base64.b85decode(blob)
    raw = bytes(b ^ _LLAVE[i % len(_LLAVE)] for i, b in enumerate(xored))
    return json.loads(raw)["ca"]

_CFG = _cargar_mapa()

@dataclass
class AplicacionCFDI:
    cfdi: CFDI
    movimiento: dict
    importe_aplicado: float

@dataclass
class PropuestaConciliacion:
    tipo_match: str    # 'exacto' | 'combinado_facturas' | 'combinado_movimientos' | 'pago_parcial' | 'monto_sin_rfc'
    confianza: int
    motivo: str
    cfdis: list = field(default_factory=list)
    movimientos: list = field(default_factory=list)
    aplicaciones: list = field(default_factory=list)  # list[AplicacionCFDI]

def _combinaciones_que_suman(indices_y_montos, objetivo, tolerancia=0.01, max_items=None, max_candidatos=None):
    max_items = _CFG["max_items"] if max_items is None else max_items
    max_candidatos = _CFG["max_cand"] if max_candidatos is None else max_candidatos
    candidatos = indices_y_montos[:max_candidatos]
    for tam in range(2, min(max_items, len(candidatos)) + 1):
        for combo in itertools.combinations(candidatos, tam):
            if abs(sum(m for _, m in combo) - objetivo) <= tolerancia:
                return [i for i, _ in combo]
    return None

def conciliar_avanzado(
    cfdis: list[CFDI],
    movimientos: list[dict],
    rfc_empresa: str,
    ventana_dias: int = None,
    tolerancia_monto: float = None,
    tipo_por_uuid: Optional[dict] = None,
) -> list[PropuestaConciliacion]:
    ventana_dias = _CFG["ventana"] if ventana_dias is None else ventana_dias
    tolerancia_monto = _CFG["tol"] if tolerancia_monto is None else tolerancia_monto
    propuestas = []
    tipo_por_uuid = tipo_por_uuid or {}

    import re
    movs_conciliados_por_folio = set()
    for i, mov in enumerate(movimientos):
        num_fact_raw = str(mov.get("numero_factura") or "").strip()
        num_fact_limpio = re.sub(r'[^a-zA-Z0-9]', '', num_fact_raw).upper()
        if not num_fact_limpio:
            continue
        rfc_mov = (mov.get("rfc_contraparte") or "").strip().upper()
        if rfc_mov in {"ND", "N/A", "NA", "N.D.", "N.A.", "XAXX010101000", "XEXX010101000", "000000000000", "XXX"}:
            rfc_mov = ""

        for cfdi in cfdis:
            # Excluir comprobantes que no son facturas ni pagos (ej. Nómina 'N', Traslado 'T', Egreso 'E')
            if getattr(cfdi, "tipo_comprobante", "I") not in ("I", "P"):
                continue

            tipo = tipo_por_uuid.get(cfdi.uuid) or clasificar_cfdi(cfdi, rfc_empresa)
            if tipo != "desconocido":
                tipo_mov_esperado = "ingreso" if tipo == "emitido" else "egreso"
                if mov["tipo"] != tipo_mov_esperado:
                    continue

            rfc_contraparte = cfdi.rfc_receptor if tipo == "emitido" else cfdi.rfc_emisor
            # REGLA DURA: Si el movimiento trae RFC, no puede casarse con CFDI de otro RFC
            if rfc_mov and rfc_mov != rfc_contraparte:
                continue

            folio_limpio = re.sub(r'[^a-zA-Z0-9]', '', cfdi.folio or "").upper()
            serie_limpia = re.sub(r'[^a-zA-Z0-9]', '', cfdi.serie or "").upper()
            uuid_limpio = re.sub(r'[^a-zA-Z0-9]', '', cfdi.uuid or "").upper()

            coincide_folio = False
            # Exigir que el folio no sea ambiguo (mínimo 2 caracteres o coincidencia con serie)
            if folio_limpio and len(folio_limpio) >= 2 and num_fact_limpio == folio_limpio:
                coincide_folio = True
            elif (serie_limpia or folio_limpio) and num_fact_limpio == (serie_limpia + folio_limpio):
                coincide_folio = True
            elif len(num_fact_limpio) >= 8 and (num_fact_limpio == uuid_limpio or uuid_limpio.startswith(num_fact_limpio)):
                coincide_folio = True

            if coincide_folio:
                importe_aplicar = min(float(mov["total"]), float(cfdi.total)) if cfdi.total else float(mov["total"])
                propuestas.append(PropuestaConciliacion(
                    tipo_match="exacto",
                    confianza=_CFG["cf"].get("exacto", 100),
                    motivo=f"Asociación directa por número de factura ({cfdi.serie or ''}{cfdi.folio or ''}) en el movimiento.",
                    cfdis=[cfdi],
                    movimientos=[mov],
                    aplicaciones=[AplicacionCFDI(cfdi, mov, importe_aplicar)],
                ))
                movs_conciliados_por_folio.add(id(mov))
                break

    grupos = {}  # (tipo_mov_esperado, rfc_contraparte) -> {"cfdis": [...], "movs": [(idx, mov), ...]}

    for cfdi in cfdis:
        if getattr(cfdi, "tipo_comprobante", "I") not in ("I", "P"):
            continue
        tipo = tipo_por_uuid.get(cfdi.uuid) or clasificar_cfdi(cfdi, rfc_empresa)
        if tipo == "desconocido":
            continue
        tipo_mov_esperado = "ingreso" if tipo == "emitido" else "egreso"
        rfc_contraparte = cfdi.rfc_receptor if tipo == "emitido" else cfdi.rfc_emisor
        clave = (tipo_mov_esperado, rfc_contraparte)
        grupos.setdefault(clave, {"cfdis": [], "movs": []})["cfdis"].append(cfdi)

    movs_sin_grupo = []
    for i, mov in enumerate(movimientos):
        if id(mov) in movs_conciliados_por_folio:
            continue
        rfc_mov = (mov.get("rfc_contraparte") or "").strip().upper()
        if rfc_mov in {"ND", "N/A", "NA", "N.D.", "N.A.", "XAXX010101000", "XEXX010101000", "000000000000", "XXX"}:
            rfc_mov = ""
        clave = (mov["tipo"], rfc_mov) if rfc_mov else None
        if clave and clave in grupos:
            grupos[clave]["movs"].append((i, mov))
        else:
            movs_sin_grupo.append((i, mov))

    for (tipo_mov, rfc), datos in grupos.items():
        cfdis_grupo = list(datos["cfdis"])
        movs_grupo = list(datos["movs"])
        usados_cfdi, usados_mov = set(), set()

        for cfdi in cfdis_grupo:
            if id(cfdi) in usados_cfdi:
                continue
            # Verificar si hay múltiples candidatos del mismo monto para desambiguar
            candidatos_exactos = [
                (idx, mov) for idx, mov in movs_grupo
                if idx not in usados_mov
                and abs(mov["total"] - cfdi.total) <= tolerancia_monto
                and _dentro_de_ventana(cfdi.fecha, mov["fecha"], ventana_dias)
            ]
            cfdis_mismo_monto = [
                c for c in cfdis_grupo
                if id(c) not in usados_cfdi
                and abs(c.total - cfdi.total) <= tolerancia_monto
            ]

            if len(candidatos_exactos) == 1 and len(cfdis_mismo_monto) == 1:
                idx, mov = candidatos_exactos[0]
                propuestas.append(PropuestaConciliacion(
                    tipo_match="exacto", confianza=_CFG["cf"]["exacto"],
                    motivo=f"RFC e importe coinciden exactamente (${cfdi.total:,.2f}) como candidato único.",
                    cfdis=[cfdi], movimientos=[mov],
                    aplicaciones=[AplicacionCFDI(cfdi, mov, cfdi.total)],
                ))
                usados_cfdi.add(id(cfdi))
                usados_mov.add(idx)
            elif len(candidatos_exactos) >= 1:
                # Caso ambiguo: múltiples facturas del mismo monto o múltiples pagos iguales
                idx, mov = candidatos_exactos[0]
                propuestas.append(PropuestaConciliacion(
                    tipo_match="exacto", confianza=80,
                    motivo=f"RFC e importe coinciden (${cfdi.total:,.2f}), pero existen {len(cfdis_mismo_monto)} facturas del mismo monto. Requiere verificación de folio.",
                    cfdis=[cfdi], movimientos=[mov],
                    aplicaciones=[AplicacionCFDI(cfdi, mov, cfdi.total)],
                ))
                usados_cfdi.add(id(cfdi))
                usados_mov.add(idx)

        restantes_cfdi = [c for c in cfdis_grupo if id(c) not in usados_cfdi]
        for idx, mov in movs_grupo:
            if idx in usados_mov:
                continue
            disponibles = [(j, c.total) for j, c in enumerate(restantes_cfdi) if id(c) not in usados_cfdi]
            combo = _combinaciones_que_suman(disponibles, mov["total"], tolerancia_monto)
            if combo:
                cfdis_combo = [restantes_cfdi[j] for j in combo]
                folios = ", ".join(f"{c.serie or ''}{c.folio or ''}" for c in cfdis_combo)
                propuestas.append(PropuestaConciliacion(
                    tipo_match="combinado_facturas", confianza=_CFG["cf"]["n_fact"],
                    motivo=(f"La suma de {len(cfdis_combo)} facturas ({folios}) coincide "
                            f"exactamente con este movimiento (${mov['total']:,.2f}). "
                            f"Parece un pago que cubrió varias facturas juntas."),
                    cfdis=cfdis_combo, movimientos=[mov],
                    aplicaciones=[AplicacionCFDI(c, mov, c.total) for c in cfdis_combo],
                ))
                for j in combo:
                    usados_cfdi.add(id(restantes_cfdi[j]))
                usados_mov.add(idx)

        restantes_cfdi = [c for c in cfdis_grupo if id(c) not in usados_cfdi]
        restantes_mov = [(idx, mov) for idx, mov in movs_grupo if idx not in usados_mov]
        for cfdi in restantes_cfdi:
            if id(cfdi) in usados_cfdi:
                continue
            disponibles = [(pos, mov["total"]) for pos, (idx, mov) in enumerate(restantes_mov) if idx not in usados_mov]
            combo = _combinaciones_que_suman(disponibles, cfdi.total, tolerancia_monto)
            if combo:
                movs_combo = [restantes_mov[pos] for pos in combo]
                fechas_combo = [m["fecha"] for _, m in movs_combo]
                dispersion_dias = (max(fechas_combo) - min(fechas_combo)).days

                if dispersion_dias <= _CFG["disp_dias"]:
                    explicacion = (
                        "Común cuando varios cargos (ej. comisiones bancarias) se "
                        "facturan juntos una vez al mes."
                    )
                else:
                    explicacion = (
                        "Parecen abonos en fechas distintas que, sumados, liquidan "
                        "esta factura por completo (pagos parciales acumulados)."
                    )

                propuestas.append(PropuestaConciliacion(
                    tipo_match="combinado_movimientos", confianza=_CFG["cf"]["n_mov"],
                    motivo=(f"La suma de {len(movs_combo)} movimientos coincide con la "
                            f"factura {cfdi.serie or ''}{cfdi.folio or ''} (${cfdi.total:,.2f}). "
                            f"{explicacion}"),
                    cfdis=[cfdi], movimientos=[m for _, m in movs_combo],
                    aplicaciones=[AplicacionCFDI(cfdi, m, m["total"]) for _, m in movs_combo],
                ))
                usados_cfdi.add(id(cfdi))
                for pos in combo:
                    idx, _ = restantes_mov[pos]
                    usados_mov.add(idx)

        restantes_cfdi = [c for c in cfdis_grupo if id(c) not in usados_cfdi]
        restantes_mov = sorted(
            [(idx, mov) for idx, mov in movs_grupo if idx not in usados_mov],
            key=lambda x: x[1]["fecha"],
        )
        saldos = {id(c): c.total for c in restantes_cfdi}
        for idx, mov in restantes_mov:
            candidata = next(
                (c for c in restantes_cfdi if saldos[id(c)] > tolerancia_monto
                 and mov["total"] <= saldos[id(c)] + tolerancia_monto),
                None,
            )
            if candidata is None:
                continue
            saldo_antes = saldos[id(candidata)]
            saldos[id(candidata)] = round(saldo_antes - mov["total"], 2)
            liquidada = saldos[id(candidata)] <= tolerancia_monto
            propuestas.append(PropuestaConciliacion(
                tipo_match="pago_parcial", confianza=_CFG["cf"]["parcial"],
                motivo=(f"Pago parcial de la factura {candidata.serie or ''}{candidata.folio or ''} "
                        f"(total ${candidata.total:,.2f}): este movimiento cubre "
                        f"${mov['total']:,.2f}. "
                        + (f"Saldo liquidado." if liquidada
                           else f"Saldo pendiente: ${saldos[id(candidata)]:,.2f}.")),
                cfdis=[candidata], movimientos=[mov],
                aplicaciones=[AplicacionCFDI(candidata, mov, mov["total"])],
            ))
            usados_mov.add(idx)
            if liquidada:
                usados_cfdi.add(id(candidata))

    cfdis_ya_usados_ids = {id(c) for p in propuestas for c in p.cfdis}
    cfdis_disponibles_global = [c for c in cfdis if id(c) not in cfdis_ya_usados_ids]

    for idx, mov in movs_sin_grupo:
        rfc_mov = (mov.get("rfc_contraparte") or "").strip().upper()
        if rfc_mov in {"ND", "N/A", "NA", "N.D.", "N.A.", "XAXX010101000", "XEXX010101000", "000000000000", "XXX"}:
            rfc_mov = ""
        # REGLA DURA: Si el movimiento trae su propio RFC del banco, JAMÁS asociar por monto a un CFDI de otro RFC
        if rfc_mov:
            continue

        candidatos_monto = []
        for cfdi in cfdis_disponibles_global:
            if id(cfdi) in cfdis_ya_usados_ids:
                continue
            tipo = tipo_por_uuid.get(cfdi.uuid) or clasificar_cfdi(cfdi, rfc_empresa)
            if tipo == "desconocido":
                continue
            tipo_mov_esperado = "ingreso" if tipo == "emitido" else "egreso"
            if mov["tipo"] != tipo_mov_esperado:
                continue
            if (abs(mov["total"] - cfdi.total) <= tolerancia_monto
                    and _dentro_de_ventana(cfdi.fecha, mov["fecha"], ventana_dias)):
                candidatos_monto.append(cfdi)

        if len(candidatos_monto) == 1:
            cfdi = candidatos_monto[0]
            propuestas.append(PropuestaConciliacion(
                tipo_match="monto_sin_rfc", confianza=_CFG["cf"]["sin_rfc"],
                motivo=(f"El movimiento no trae RFC, pero el importe (${cfdi.total:,.2f}) "
                        f"y la fecha coinciden exactamente con la factura {cfdi.serie or ''}{cfdi.folio or ''}."),
                cfdis=[cfdi], movimientos=[mov],
                aplicaciones=[AplicacionCFDI(cfdi, mov, cfdi.total)],
            ))
            cfdis_ya_usados_ids.add(id(cfdi))
        elif len(candidatos_monto) > 1:
            cfdi = candidatos_monto[0]
            propuestas.append(PropuestaConciliacion(
                tipo_match="monto_sin_rfc", confianza=50,
                motivo=(f"Ambigüedad: existen {len(candidatos_monto)} facturas por ${mov['total']:,.2f} "
                        f"sin RFC en el banco. Requiere asignación del contador."),
                cfdis=[cfdi], movimientos=[mov],
                aplicaciones=[AplicacionCFDI(cfdi, mov, cfdi.total)],
            ))
            cfdis_ya_usados_ids.add(id(cfdi))

    cfdi_saldos = {id(c): c.total for c in cfdis}
    for p in propuestas:
        for app in p.aplicaciones:
            cfdi_saldos[id(app.cfdi)] -= app.importe_aplicado

    cfdis_disponibles_global = [c for c in cfdis if cfdi_saldos[id(c)] > 0.01]
    
    movs_ya_usados_ids = {id(m) for p in propuestas for m in p.movimientos}
    movs_restantes = [m for m in movimientos if id(m) not in movs_ya_usados_ids]

    import re
    for mov in movs_restantes:
        rfc_mov = (mov.get("rfc_contraparte") or "").strip().upper()
        if rfc_mov in {"ND", "N/A", "NA", "N.D.", "N.A.", "XAXX010101000", "XEXX010101000", "000000000000", "XXX"}:
            rfc_mov = ""
        desc_limpia = re.sub(r'[^a-zA-Z0-9]', '', f"{mov.get('descripcion', '')}{mov.get('referencia', '')}{mov.get('numero_factura', '')}").upper()
        if not desc_limpia: continue
        
        for cfdi in cfdis_disponibles_global:
            saldo_cfdi = cfdi_saldos[id(cfdi)]
            if saldo_cfdi <= 0.01:
                continue
            
            tipo = tipo_por_uuid.get(cfdi.uuid) or clasificar_cfdi(cfdi, rfc_empresa)
            if tipo == "desconocido":
                continue
            tipo_mov_esperado = "ingreso" if tipo == "emitido" else "egreso"
            if mov["tipo"] != tipo_mov_esperado:
                continue

            rfc_contraparte = cfdi.rfc_receptor if tipo == "emitido" else cfdi.rfc_emisor
            # REGLA DURA: si trae RFC del banco, no puede asociarse a otro RFC
            if rfc_mov and rfc_mov != rfc_contraparte:
                continue
                
            # 1. Busqueda por Serie + Folio
            match_texto = False
            motivo_texto = ""
            
            folio_limpio = re.sub(r'[^a-zA-Z0-9]', '', cfdi.folio or "").upper()
            serie_limpia = re.sub(r'[^a-zA-Z0-9]', '', cfdi.serie or "").upper()
            
            if folio_limpio and len(folio_limpio) >= 2:
                # Si menciona la serie y el folio juntos (ej. F1234)
                if serie_limpia and (serie_limpia + folio_limpio) in desc_limpia:
                    match_texto = True
                    motivo_texto = f"El estado de cuenta menciona el folio {cfdi.serie}{cfdi.folio}."
                # O si solo menciona el folio (y es razonablemente largo para no ser falso positivo)
                elif len(folio_limpio) >= 3 and folio_limpio in desc_limpia:
                    match_texto = True
                    motivo_texto = f"El estado de cuenta menciona el folio {cfdi.folio}."

            # 2. Busqueda por Nombre del Cliente/Proveedor con filtro estricto anti-falsos positivos
            if not match_texto:
                nombre = cfdi.nombre_receptor if tipo_mov_esperado == "ingreso" else cfdi.nombre_emisor
                if nombre:
                    stop_palabras_nombre = {
                        'COMPANIA', 'GRUPO', 'COMERCIAL', 'SERVICIOS', 'SISTEMAS', 'DE', 'CV', 'SA', 'SAPI', 'RL',
                        'MEXICO', 'NACIONAL', 'DISTRIBUIDORA', 'SOLUCIONES', 'CORPORATIVO', 'OPERADORA', 'ASOCIADOS',
                        'LOGISTICA', 'CONSULTORES', 'ADMINISTRACION', 'CENTRO', 'INSTITUTO', 'UNION', 'INTEGRAL', 'MEXICANA'
                    }
                    partes_nombre = [
                        p for p in re.sub(r'[^a-zA-Z0-9]', ' ', nombre).upper().split()
                        if len(p) >= 4 and p not in stop_palabras_nombre
                    ]
                    if len(partes_nombre) >= 2:
                        coincidencias = sum(1 for p in partes_nombre if p in desc_limpia)
                        if coincidencias >= 2:
                            match_texto = True
                            motivo_texto = f"El estado de cuenta menciona el nombre del tercero ({' '.join(partes_nombre[:2])})."
                    elif len(partes_nombre) == 1 and len(partes_nombre[0]) >= 6:
                        if partes_nombre[0] in desc_limpia:
                            match_texto = True
                            motivo_texto = f"El estado de cuenta menciona al tercero ({partes_nombre[0]})."

            # Si hubo coincidencia de texto, validar montos logicos
            if match_texto and mov["total"] <= saldo_cfdi + 1.0:
                importe_aplicar = min(mov["total"], saldo_cfdi)
                propuestas.append(PropuestaConciliacion(
                    tipo_match="texto_inteligente", confianza=65,
                    motivo=f"Inteligencia Lógica: {motivo_texto} El importe del banco cabe en el saldo restante del CFDI.",
                    cfdis=[cfdi], movimientos=[mov],
                    aplicaciones=[AplicacionCFDI(cfdi, mov, importe_aplicar)],
                ))
                cfdi_saldos[id(cfdi)] -= importe_aplicar
                break

    return propuestas

