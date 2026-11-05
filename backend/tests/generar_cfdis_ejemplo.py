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
"""Genera CFDIs de ejemplo (XML válidos, CFDI 4.0 con timbre fiscal) para
probar cfdi_matcher.py contra los movimientos del estado de cuenta de
ejemplo. Los folios/fechas/importes están hechos a propósito para
coincidir con los movimientos de Proveedor ABC y del cliente
Comercializadora del Norte en generar_estado_cuenta_ejemplo.py."""

import os

RFC_EMPRESA = "EDP930101AB1"  # RFC de la empresa demo (receptora/emisora según el caso)

PLANTILLA_CFDI = """<?xml version="1.0" encoding="UTF-8"?>
<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/4"
    xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital"
    Version="4.0" Fecha="{fecha}" Total="{total}" SubTotal="{subtotal}"
    Serie="{serie}" Folio="{folio}" TipoDeComprobante="I" Moneda="MXN"
    LugarExpedicion="45000">
  <cfdi:Emisor Rfc="{rfc_emisor}" Nombre="{nombre_emisor}" RegimenFiscal="601"/>
  <cfdi:Receptor Rfc="{rfc_receptor}" Nombre="{nombre_receptor}"
      DomicilioFiscalReceptor="45000" RegimenFiscalReceptor="601" UsoCFDI="G03"/>
  <cfdi:Impuestos TotalImpuestosTrasladados="{iva_importe}">
    <cfdi:Traslados>
      <cfdi:Traslado Base="{base}" Impuesto="002" TipoFactor="Tasa"
          TasaOCuota="0.160000" Importe="{iva_importe}"/>
    </cfdi:Traslados>
  </cfdi:Impuestos>
  <cfdi:Complemento>
    <tfd:TimbreFiscalDigital xmlns:tfd="http://www.sat.gob.mx/TimbreFiscalDigital"
        Version="1.1" UUID="{uuid}" FechaTimbrado="{fecha}"
        RfcProvCertif="SAT970701NN3" SelloCFD="" NoCertificadoSAT="00001"/>
  </cfdi:Complemento>
</cfdi:Comprobante>
"""

def _generar(ruta, uuid, rfc_emisor, nombre_emisor, rfc_receptor, nombre_receptor,
             fecha, total, serie, folio):
    base = round(total / 1.16, 2)
    iva = round(total - base, 2)
    contenido = PLANTILLA_CFDI.format(
        fecha=fecha, total=f"{total:.2f}", subtotal=f"{base:.2f}",
        serie=serie, folio=folio, rfc_emisor=rfc_emisor, nombre_emisor=nombre_emisor,
        rfc_receptor=rfc_receptor, nombre_receptor=nombre_receptor,
        base=f"{base:.2f}", iva_importe=f"{iva:.2f}", uuid=uuid,
    )
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(contenido)

def generar_cfdis_ejemplo(carpeta_salida: str):
    os.makedirs(carpeta_salida, exist_ok=True)

    # --- Recibidos (proveedor ABC nos factura; nosotros somos receptor) ---
    _generar(
        os.path.join(carpeta_salida, "recibido_proveedor_abc_1.xml"),
        uuid="A1B2C3D4-0001-4E5F-8A9B-000000000001",
        rfc_emisor="ABC010101XXX", nombre_emisor="PROVEEDOR ABC SA DE CV",
        rfc_receptor=RFC_EMPRESA, nombre_receptor="EMPRESA DEMO SA DE CV",
        fecha="2026-08-02T12:00:00", total=20000.00, serie="A", folio="1042",
    )
    _generar(
        os.path.join(carpeta_salida, "recibido_proveedor_abc_2.xml"),
        uuid="A1B2C3D4-0002-4E5F-8A9B-000000000002",
        rfc_emisor="ABC010101XXX", nombre_emisor="PROVEEDOR ABC SA DE CV",
        rfc_receptor=RFC_EMPRESA, nombre_receptor="EMPRESA DEMO SA DE CV",
        fecha="2026-08-05T12:00:00", total=20000.00, serie="A", folio="1078",
    )

    # --- Emitidos (nosotros facturamos al cliente Comercializadora del Norte) ---
    _generar(
        os.path.join(carpeta_salida, "emitido_cliente_norte_1.xml"),
        uuid="A1B2C3D4-0003-4E5F-8A9B-000000000003",
        rfc_emisor=RFC_EMPRESA, nombre_emisor="EMPRESA DEMO SA DE CV",
        rfc_receptor="CDN980101XYZ", nombre_receptor="COMERCIALIZADORA DEL NORTE SA DE CV",
        fecha="2026-08-03T10:00:00", total=58000.00, serie="F", folio="8831",
    )
    _generar(
        os.path.join(carpeta_salida, "emitido_cliente_norte_2.xml"),
        uuid="A1B2C3D4-0004-4E5F-8A9B-000000000004",
        rfc_emisor=RFC_EMPRESA, nombre_emisor="EMPRESA DEMO SA DE CV",
        rfc_receptor="CDN980101XYZ", nombre_receptor="COMERCIALIZADORA DEL NORTE SA DE CV",
        fecha="2026-08-05T10:00:00", total=32000.00, serie="F", folio="8902",
    )

    return carpeta_salida

if __name__ == "__main__":
    ruta = generar_cfdis_ejemplo(os.path.join(os.path.dirname(__file__), "cfdi_ejemplo"))
    print(f"CFDIs generados en: {ruta}")

