from typing import List, Dict, Any
from datetime import datetime
import re

from exporters.contpaqi_bridge_client import (
    exportar_polizas,
    ErrorBridgeContpaqi,
)

def exportar_polizas_via_sdk(movimientos_poliza: list, nombre_interno_empresa: str) -> dict[str, Any]:
    """
    Exporta pólizas y sus movimientos directamente a CONTPAQi Contabilidad
    usando el microservicio C# SDK (puente local).
    """
    if not movimientos_poliza:
        return {"exito": True, "mensaje": "No hay pólizas para exportar.", "polizas_procesadas": 0}

    data: dict[str, Any] = {
        "empresa": nombre_interno_empresa,
        "polizas": []
    }

    for mov in movimientos_poliza:
        tipo_str = str(mov.tipo).lower()
        tipo_int = 3  # diario por defecto
        if "ingreso" in tipo_str:
            tipo_int = 1
        elif "egreso" in tipo_str:
            tipo_int = 2

        tiene_cfdi = bool(getattr(mov, "cfdi_uuid", None) or getattr(mov, "facturas", None))
        tiene_iva = getattr(mov, "tiene_iva", False)
        if (not tiene_cfdi) and tiene_iva:
            xml_tag = " (ADJUNTAR XML)"
        else:
            xml_tag = " (OPE sistema)"
        desc_limpia = re.sub(r"\s*\((?:adjuntar\s*xml|ope\s*sistema)\)", "", mov.descripcion or "", flags=re.IGNORECASE).strip()
        concepto_poliza = f"{desc_limpia}{xml_tag}".strip()

        # Determinar cuenta de contraparte para el Control de IVA (F4 / F6)
        banco_clean = str(getattr(mov, "cuenta_banco", "") or "").replace("-", "").replace(" ", "").strip()
        cuenta_contraparte = ""
        for linea in getattr(mov, "lineas", []):
            c_clean = str(linea.cuenta or "").replace("-", "").replace(" ", "").strip()
            if c_clean and c_clean != banco_clean:
                # Priorizar cuentas que no sean de IVA acreditable/trasladado directo si hay cuenta de proveedor/gasto
                cuenta_contraparte = c_clean
                if not (c_clean.startswith("118") or c_clean.startswith("119") or c_clean.startswith("208") or c_clean.startswith("209")):
                    break

        # Recopilar UUIDs
        uuids = []
        if getattr(mov, "cfdi_uuid", None):
            u_clean = str(mov.cfdi_uuid).strip().upper()
            if u_clean:
                uuids.append(u_clean)
        if getattr(mov, "facturas", None):
            for f in mov.facturas:
                u_f = str(getattr(f, "uuid", "") or "").strip().upper()
                if u_f and u_f not in uuids:
                    uuids.append(u_f)

        # Construir registros de Control de IVA (F4 para egresos/diarios, F6 para ingresos)
        control_iva_lista = []
        afectar_iva = bool(getattr(mov, "afectar_iva_fiscal", True))

        if afectar_iva:
            if getattr(mov, "facturas", None) and len(mov.facturas) > 0:
                for f in mov.facturas:
                    base_gravada = 0.0
                    iva_importe = 0.0
                    ret_iva = 0.0
                    ret_isr = 0.0
                    tasa_val = 16.0

                    for imp in getattr(f, "impuestos", []):
                        if getattr(imp, "es_retencion", False):
                            tipo_imp = str(getattr(imp, "tipo_impuesto", "") or "").upper()
                            if "IVA" in tipo_imp:
                                ret_iva += float(imp.importe or 0)
                            elif "ISR" in tipo_imp:
                                ret_isr += float(imp.importe or 0)
                        else:
                            base_gravada += float(imp.base or 0)
                            iva_importe += float(imp.importe or 0)
                            tasa_raw = float(getattr(imp, "tasa", 0.16) or 0.16)
                            tasa_val = tasa_raw * 100.0 if tasa_raw <= 1.0 else tasa_raw

                    total_f = base_gravada + iva_importe - ret_iva - ret_isr
                    # Solo incluir si hay IVA gravado o retenciones reales (no agregar F4 si IVA es 0)
                    if iva_importe > 0 or ret_iva > 0 or ret_isr > 0:
                        control_iva_lista.append({
                            "uuid": getattr(f, "uuid", "") or "",
                            "serie": getattr(f, "serie", "") or "",
                            "folio": getattr(f, "folio", "") or "",
                            "cuenta": cuenta_contraparte,
                            "base": round(base_gravada, 2),
                            "iva": round(iva_importe, 2),
                            "total": round(total_f, 2),
                            "tasa": round(tasa_val, 2),
                            "retencionIva": round(ret_iva, 2),
                            "retencionIsr": round(ret_isr, 2),
                        })
            elif getattr(mov, "cfdi_impuestos", None) and len(mov.cfdi_impuestos) > 0:
                base_gravada = 0.0
                iva_importe = 0.0
                tasa_val = 16.0
                for imp in mov.cfdi_impuestos:
                    base_gravada += float(getattr(imp, "base", 0) or 0)
                    iva_importe += float(getattr(imp, "importe", 0) or 0)
                    tasa_raw = float(getattr(imp, "tasa", 0.16) or 0.16)
                    tasa_val = tasa_raw * 100.0 if tasa_raw <= 1.0 else tasa_raw

                # Solo incluir si hay IVA gravado (> 0)
                if iva_importe > 0:
                    control_iva_lista.append({
                        "uuid": str(getattr(mov, "cfdi_uuid", "") or "").strip().upper(),
                        "serie": "",
                        "folio": "",
                        "cuenta": cuenta_contraparte,
                        "base": round(base_gravada, 2),
                        "iva": round(iva_importe, 2),
                        "total": round(base_gravada + iva_importe, 2),
                        "tasa": round(tasa_val, 2),
                        "retencionIva": 0.0,
                        "retencionIsr": 0.0,
                    })

        if len(control_iva_lista) == 0:
            afectar_iva = False

        p = {
            "numero": str(mov.numero_poliza),
            "tipo": tipo_int,
            "fecha": mov.fecha.strftime("%Y-%m-%d"),
            "concepto": concepto_poliza,
            "movimientos": [],
            "cfdiUuids": uuids,
            "controlIva": control_iva_lista,
            "afectarIvaFiscal": afectar_iva
        }

        for linea in mov.lineas:
            p["movimientos"].append({
                "cuenta": str(linea.cuenta).replace("-", "").replace(" ", "").strip(),
                "tipoMovto": 1 if linea.naturaleza.lower() == "cargo" else 2,
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
        return {
            "exito": False,
            "mensaje": f"Error comunicando con CONTPAQi Bridge: {e}",
            "polizas_procesadas": 0,
        }
    except Exception as e:
        return {
            "exito": False,
            "mensaje": f"Error inesperado al exportar: {e}",
            "polizas_procesadas": 0,
        }
