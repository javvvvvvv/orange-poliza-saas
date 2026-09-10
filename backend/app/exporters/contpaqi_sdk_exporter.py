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
from contpaqi_bridge_client import ErrorBridgeContpaqi, exportar_polizas


def exportar_polizas_via_sdk(movimientos_poliza, nombre_interno_empresa: str) -> dict:
    """
    Exporta las pólizas llamando al bridge de ContpaqiBridge (C#).

    `nombre_interno_empresa` DEBE ser el nombre interno de base de datos de
    CONTPAQi (columna `empresas.base_datos_contpaqi`, ej.
    "ctADRIANA_MARCELA_PACHECO_MONARREZ"), nunca el nombre de exhibición
    que ve el usuario -- confirmado en pruebas que abreEmpresa() con el
    nombre bonito no abre nada.
    """
    if not nombre_interno_empresa:
        return {
            "exito": False,
            "mensaje": "Falta configurar el nombre interno de CONTPAQi para esta empresa "
                       "(Configuración > Catálogo, base de datos CONTPAQi).",
            "polizas_procesadas": 0,
        }

    data = {"empresa": nombre_interno_empresa, "polizas": []}

    for mov in movimientos_poliza:
        tipo_str = str(mov.tipo).lower()
        tipo_int = 3  # diario por defecto
        if "ingreso" in tipo_str:
            tipo_int = 1
        elif "egreso" in tipo_str:
            tipo_int = 2

        p = {
            "numero": str(mov.numero_poliza),
            "tipo": tipo_int,
            "fecha": mov.fecha.strftime("%Y-%m-%d"),
            "concepto": mov.descripcion,
            "movimientos": [],
            "cfdi_uuids": []
        }
        
        uuids = []
        if getattr(mov, "cfdi_uuid", None):
            uuids.append(mov.cfdi_uuid)
        if getattr(mov, "facturas", None):
            for f in mov.facturas:
                uuids.append(f.uuid)
        p["cfdi_uuids"] = list(set(uuids))
        for linea in mov.lineas:
            p["movimientos"].append({
                "cuenta": str(linea.cuenta).replace("-", ""),
                "tipoMovto": 0 if linea.naturaleza.lower() == "cargo" else 1,
                "importe": float(round(linea.importe, 2)),
                "concepto": str(linea.descripcion) if linea.descripcion else "",
                "referencia": str(getattr(mov, "numero_factura", "")) or "",
            })
        data["polizas"].append(p)

    try:
        res = exportar_polizas(data)
        return {
            "exito": res.get("exito", False),
            "mensaje": res.get("mensaje", ""),
            "polizas_procesadas": len(movimientos_poliza) if res.get("exito") else 0,
        }
    except ErrorBridgeContpaqi as e:
        return {"exito": False, "mensaje": str(e), "polizas_procesadas": 0}
