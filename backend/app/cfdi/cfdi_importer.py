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
"""
Importador de CFDI (facturas electrónicas del SAT, formato XML).

Lee un CFDI 3.3 o 4.0 y extrae lo que necesita el motor para asociar la
factura a un movimiento bancario y llenar el detalle de impuestos (F4):

  - UUID (folio fiscal, del complemento TimbreFiscalDigital)
  - RFC emisor / receptor
  - Fecha, Total, Subtotal
  - Impuestos trasladados: lista de {base, importe, tasa} (normalmente
    un solo IVA al 16%, pero un CFDI puede traer varios traslados)

No valida el sello ni el certificado (eso ya lo hizo el SAT al timbrar);
solo lee los datos que ya vienen en el archivo. Soporta los namespaces
de CFDI 3.3 y 4.0 porque en la práctica una empresa puede tener XMLs
viejos y nuevos mezclados en la misma carpeta.
"""

import os
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

NAMESPACES = {
    "cfdi40": "http://www.sat.gob.mx/cfd/4",
    "cfdi33": "http://www.sat.gob.mx/cfd/3",
    "tfd": "http://www.sat.gob.mx/TimbreFiscalDigital",
}

class ErrorCFDI(Exception):
    pass

@dataclass
class ImpuestoTraslado:
    base: float
    importe: float
    tasa: float          # ej. 0.16, 0.08, 0.0
    impuesto: str = "002"  # 001 = ISR, 002 = IVA, 003 = IEPS
    tipo_factor: str = "Tasa"  # 'Tasa', 'Exento', 'Cuota'

@dataclass
class ImpuestoRetenido:
    """Un nodo <Retencion> del CFDI. A diferencia de <Traslado>, el XML
    no siempre trae Base ni TasaOCuota — solo Impuesto e Importe; la base real
    se toma del SubTotal del comprobante al construir el ImpuestoPoliza."""
    importe: float
    impuesto: str = "002"  # 001 = ISR, 002 = IVA, 003 = IEPS
    base: float = 0.0
    tasa: float = 0.0

@dataclass
class CFDI:
    uuid: str
    rfc_emisor: str
    nombre_emisor: str
    rfc_receptor: str
    nombre_receptor: str
    fecha: datetime
    total: float
    subtotal: float
    serie: Optional[str]
    folio: Optional[str]
    tipo_comprobante: str  # 'I' ingreso, 'E' egreso, 'P' pago, 'N' nómina, 'T' traslado
    impuestos_trasladados: list = field(default_factory=list)  # list[ImpuestoTraslado]
    impuestos_retenidos: list = field(default_factory=list)  # list[ImpuestoRetenido]
    uuids_relacionados: list = field(default_factory=list) # UUIDs de las facturas pagadas (si es REP) o relacionadas (ej. Nota de Crédito)
    archivo_origen: str = ""
    moneda: str = "MXN"
    tipo_cambio: float = 1.0
    metodo_pago: Optional[str] = None  # 'PUE', 'PPD'
    descuento: float = 0.0

def _detectar_namespace_comprobante(raiz) -> str:
    tag = raiz.tag
    if tag.startswith("{" + NAMESPACES["cfdi40"] + "}"):
        return "cfdi40"
    if tag.startswith("{" + NAMESPACES["cfdi33"] + "}"):
        return "cfdi33"
    raise ErrorCFDI(f"No se reconoce el namespace del comprobante: {tag}")

def importar_cfdi(ruta_xml: str) -> CFDI:
    """Lee un archivo CFDI XML y regresa un objeto CFDI con los datos
    normalizados. Lanza ErrorCFDI si el archivo no es un CFDI válido o
    le falta el timbre fiscal (UUID)."""
    try:
        arbol = ET.parse(ruta_xml)
    except ET.ParseError as e:
        raise ErrorCFDI(f"El archivo '{ruta_xml}' no es un XML válido: {e}")

    raiz = arbol.getroot()
    ns_key = _detectar_namespace_comprobante(raiz)
    ns_cfdi = NAMESPACES[ns_key]

    def attr(nombre, default=None):
        return raiz.get(nombre, default)

    fecha_str = attr("Fecha")
    try:
        fecha = datetime.strptime(fecha_str[:19], "%Y-%m-%dT%H:%M:%S")
    except (TypeError, ValueError):
        raise ErrorCFDI(f"Fecha inválida en el CFDI '{ruta_xml}': {fecha_str!r}")

    emisor = raiz.find(f"{{{ns_cfdi}}}Emisor")
    receptor = raiz.find(f"{{{ns_cfdi}}}Receptor")
    if emisor is None or receptor is None:
        raise ErrorCFDI(f"El CFDI '{ruta_xml}' no trae nodo Emisor/Receptor.")

    complemento = raiz.find(f"{{{ns_cfdi}}}Complemento")
    uuid = None
    if complemento is not None:
        timbre = complemento.find(f"{{{NAMESPACES['tfd']}}}TimbreFiscalDigital")
        if timbre is not None:
            uuid = timbre.get("UUID")

    if not uuid:
        raise ErrorCFDI(
            f"El CFDI '{ruta_xml}' no tiene UUID (timbre fiscal). "
            f"¿Es un XML sin timbrar?"
        )

    tipo_comprobante = attr("TipoDeComprobante", "I").upper()
    total = round(float(attr("Total", 0)), 2)
    subtotal = round(float(attr("SubTotal", 0)), 2)
    descuento = round(float(attr("Descuento", 0)), 2)
    moneda = attr("Moneda", "MXN").upper()
    try:
        tipo_cambio = float(attr("TipoCambio", 1.0))
    except (ValueError, TypeError):
        tipo_cambio = 1.0
    metodo_pago = attr("MetodoPago")
    traslados = []
    retenciones = []
    
    uuids_relacionados = []

    # Extraer CFDIS Relacionados (ej. en Notas de Crédito 'E')
    for nodo_rel in raiz.findall(f"{{{ns_cfdi}}}CfdiRelacionados"):
        for rel in nodo_rel.findall(f"{{{ns_cfdi}}}CfdiRelacionado"):
            u_rel = rel.get("UUID")
            if u_rel:
                uuids_relacionados.append(u_rel.strip().upper())
    
    if tipo_comprobante == "P" and total == 0:
        if complemento is not None:
            # Buscar complemento de pagos 1.0 o 2.0
            pago20_ns = "http://www.sat.gob.mx/Pagos/20"
            pago10_ns = "http://www.sat.gob.mx/Pagos"
            
            nodo_pagos20 = complemento.find(f"{{{pago20_ns}}}Pagos")
            if nodo_pagos20 is not None:
                monto_total = sum(float(p.get("Monto", 0)) for p in nodo_pagos20.findall(f"{{{pago20_ns}}}Pago"))
                total = round(monto_total, 2)
                subtotal = total
                totales = nodo_pagos20.find(f"{{{pago20_ns}}}Totales")
                if totales is not None:
                    # Totales en complemento de pagos 2.0
                    base16 = float(totales.get("TotalTrasladosBaseIVA16") or 0)
                    imp16 = float(totales.get("TotalTrasladosImpuestoIVA16") or 0)
                    if base16 > 0:
                        traslados.append(ImpuestoTraslado(base=base16, importe=imp16, tasa=0.16, impuesto="002", tipo_factor="Tasa"))
                    base08 = float(totales.get("TotalTrasladosBaseIVA8") or 0)
                    imp08 = float(totales.get("TotalTrasladosImpuestoIVA8") or 0)
                    if base08 > 0:
                        traslados.append(ImpuestoTraslado(base=base08, importe=imp08, tasa=0.08, impuesto="002", tipo_factor="Tasa"))
                    base0 = float(totales.get("TotalTrasladosBaseIVA0") or 0)
                    if base0 > 0:
                        traslados.append(ImpuestoTraslado(base=base0, importe=0.0, tasa=0.0, impuesto="002", tipo_factor="Tasa"))
                    base_ex = float(totales.get("TotalTrasladosBaseIVAExento") or 0)
                    if base_ex > 0:
                        traslados.append(ImpuestoTraslado(base=base_ex, importe=0.0, tasa=0.0, impuesto="002", tipo_factor="Exento"))
                uuids = []
                for p in nodo_pagos20.findall(f"{{{pago20_ns}}}Pago"):
                    for dr in p.findall(f"{{{pago20_ns}}}DoctoRelacionado"):
                        if dr.get("IdDocumento"):
                            uuids.append(dr.get("IdDocumento").strip().upper())
                uuids_relacionados.extend(uuids)
                        
            nodo_pagos10 = complemento.find(f"{{{pago10_ns}}}Pagos")
            if nodo_pagos10 is not None:
                monto_total = sum(float(p.get("Monto", 0)) for p in nodo_pagos10.findall(f"{{{pago10_ns}}}Pago"))
                total = round(monto_total, 2)
                subtotal = total
                uuids = []
                for p in nodo_pagos10.findall(f"{{{pago10_ns}}}Pago"):
                    for dr in p.findall(f"{{{pago10_ns}}}DoctoRelacionado"):
                        if dr.get("IdDocumento"):
                            uuids.append(dr.get("IdDocumento").strip().upper())
                uuids_relacionados.extend(uuids)

    impuestos_nodo = raiz.find(f"{{{ns_cfdi}}}Impuestos")
    if impuestos_nodo is not None:
        nodo_traslados = impuestos_nodo.find(f"{{{ns_cfdi}}}Traslados")
        if nodo_traslados is not None:
            for traslado in nodo_traslados.findall(f"{{{ns_cfdi}}}Traslado"):
                tf = traslado.get("TipoFactor", "Tasa")
                b = float(traslado.get("Base", 0))
                imp_cve = traslado.get("Impuesto", "002")
                if tf == "Exento":
                    imp = 0.0
                    tas = 0.0
                else:
                    imp = float(traslado.get("Importe", 0))
                    tas = float(traslado.get("TasaOCuota", 0))
                traslados.append(ImpuestoTraslado(
                    base=b,
                    importe=imp,
                    tasa=tas,
                    impuesto=imp_cve,
                    tipo_factor=tf,
                ))
        nodo_retenciones = impuestos_nodo.find(f"{{{ns_cfdi}}}Retenciones")
        if nodo_retenciones is not None:
            for retencion in nodo_retenciones.findall(f"{{{ns_cfdi}}}Retencion"):
                imp_cve = retencion.get("Impuesto", "002")
                imp_ret = float(retencion.get("Importe", 0))
                b_ret = float(retencion.get("Base", 0)) or subtotal
                t_ret = round(imp_ret / b_ret, 6) if b_ret > 0 else 0.0
                retenciones.append(ImpuestoRetenido(
                    importe=imp_ret,
                    impuesto=imp_cve,
                    base=b_ret,
                    tasa=t_ret,
                ))

    # Eliminar duplicados en uuids_relacionados preservando orden
    uuids_relacionados = list(dict.fromkeys(uuids_relacionados))

    return CFDI(
        uuid=uuid,
        rfc_emisor=(emisor.get("Rfc") or "").strip().upper(),
        nombre_emisor=(emisor.get("Nombre") or "").strip(),
        rfc_receptor=(receptor.get("Rfc") or "").strip().upper(),
        nombre_receptor=(receptor.get("Nombre") or "").strip(),
        fecha=fecha,
        total=total,
        subtotal=subtotal,
        serie=attr("Serie"),
        folio=attr("Folio"),
        tipo_comprobante=tipo_comprobante,
        impuestos_trasladados=traslados,
        impuestos_retenidos=retenciones,
        uuids_relacionados=uuids_relacionados,
        archivo_origen=ruta_xml,
        moneda=moneda,
        tipo_cambio=tipo_cambio,
        metodo_pago=metodo_pago,
        descuento=descuento,
    )

def importar_carpeta_cfdi(ruta_carpeta: str) -> dict:
    """Importa todos los .xml de una carpeta. Regresa los CFDIs válidos
    y una lista de errores (archivo + motivo) para los que no se
    pudieron leer, en vez de tronar toda la carga por un archivo malo."""
    import glob

    cfdis = []
    errores = []

    for ruta in sorted(glob.glob(os.path.join(ruta_carpeta, "*.xml"))):
        try:
            cfdis.append(importar_cfdi(ruta))
        except ErrorCFDI as e:
            errores.append({"archivo": ruta, "error": str(e)})

    return {"cfdis": cfdis, "errores": errores}

