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
import json
from decimal import Decimal
from datetime import datetime
from flask import Blueprint, request, jsonify, session

# Importamos el motor (se asume que sys.path ya incluye backend/app/core)
from rule_engine import encontrar_regla, Regla
from policy_generator import generar_poliza, LineaPlantilla

api_bp = Blueprint("api", __name__, url_prefix="/api/v1")

@api_bp.route("/engine/classify", methods=["POST"])
def classify_movements():
    """
    Endpoint para procesar movimientos de manera volatil (stateless).
    Requiere autenticación mediante sesión de usuario o cabecera X-API-Key.
    """
    api_key_header = request.headers.get("X-API-Key") or request.headers.get("Authorization", "")
    if api_key_header.startswith("Bearer "):
        api_key_header = api_key_header[7:].strip()
    
    server_api_key = os.environ.get("ENGINE_API_KEY") or os.environ.get("CONTPAQI_BRIDGE_API_KEY", "orange_api_key_2026")
    es_autenticado = bool(session.get("usuario_id")) or (bool(api_key_header) and api_key_header == server_api_key)
    
    if not es_autenticado:
        return jsonify({"error": "No autorizado. Se requiere inicio de sesión o cabecera X-API-Key válida."}), 401

    data = request.get_json()
    if not data:
        return jsonify({"error": "Payload JSON requerido"}), 400
        
    movimientos_crudos = data.get("movimientos", [])
    reglas_crudas = data.get("reglas", [])
    plantillas_crudas = data.get("plantillas", [])
    tasa_iva = float(data.get("tasa_iva", 0.16))
    tasa_ret_iva = float(data.get("tasa_retencion_iva", 0.0))
    tasa_ret_isr = float(data.get("tasa_retencion_isr", 0.0))
    
    # 1. Parsear reglas
    reglas_parseadas = []
    for r in reglas_crudas:
        regla = Regla(
            id=r.get("id", 0),
            empresa_id=r.get("empresa_id", 0),
            nombre=r.get("nombre", ""),
            prioridad=r.get("prioridad", 50),
            activa=bool(r.get("activa", True)),
            tipo_movimiento=r.get("tipo_movimiento") or r.get("condicion_tipo"),
            rfc_contraparte=r.get("rfc_contraparte") or r.get("condicion_rfc"),
            cuenta_bancaria_contraparte=r.get("cuenta_bancaria_contraparte") or r.get("condicion_cuenta_bancaria"),
            descripcion_exacta=r.get("descripcion_exacta") or r.get("condicion_descripcion_exacta"),
            descripcion_contiene=r.get("palabras_clave") or r.get("condicion_palabras_clave", []),
        )
        reglas_parseadas.append(regla)
        
    # 2. Parsear plantillas
    plantillas_dict = {}
    for p in plantillas_crudas:
        asientos_obj = []
        for a in p.get("asientos", p.get("lineas", [])):
            form_val = a.get("formula") or a.get("importe") or "TOTAL"
            if isinstance(form_val, str):
                form_val = form_val.strip()
                if form_val.startswith("{") and form_val.endswith("}"):
                    form_val = form_val[1:-1].strip().upper()
            asientos_obj.append(LineaPlantilla(
                cuenta=a.get("cuenta"),
                naturaleza=a.get("naturaleza") or a.get("tipo_movimiento", "cargo"),
                formula=str(form_val),
                descripcion_linea=a.get("descripcion_linea") or a.get("concepto"),
            ))
        plantillas_dict[p.get("regla_id")] = {
            "tipo_poliza": p.get("tipo_poliza", "diario"),
            "concepto_poliza": p.get("concepto_poliza", "Poliza automatica"),
            "asientos": asientos_obj,
        }

        
    resultados = []
    
    # 3. Procesar cada movimiento
    for m in movimientos_crudos:
        mov_copia = dict(m)
        if isinstance(mov_copia.get("fecha"), str):
            try:
                mov_copia["fecha"] = datetime.fromisoformat(mov_copia["fecha"].replace("Z", "+00:00"))
            except ValueError:
                pass
                
        mov_copia.setdefault("tiene_iva", True)
        mov_copia.setdefault("ret_iva", 0.0)
        mov_copia.setdefault("ret_isr", 0.0)
        mov_copia.setdefault("tipo_cambio", 1.0)
        
        match = encontrar_regla(mov_copia, reglas_parseadas)
        
        res = {
            "movimiento_original": m,
            "match": {
                "encontrado": match.regla is not None,
                "regla_id": match.regla.id if match.regla else None,
                "regla_nombre": match.regla.nombre if match.regla else None,
                "confianza": match.confianza,
                "motivo": match.motivo,
            },
            "poliza": None,
        }
        
        if match.regla is not None:
            plantilla = plantillas_dict.get(match.regla.id)
            if plantilla:
                poliza_res = generar_poliza(
                    movimiento=mov_copia, 
                    plantilla=plantilla["asientos"], 
                    nombre_regla=match.regla.nombre, 
                    motivo_match=match.motivo,
                    tasa_iva=Decimal(str(tasa_iva)),
                    tasa_ret_iva=Decimal(str(tasa_ret_iva)),
                    tasa_ret_isr=Decimal(str(tasa_ret_isr)),
                )
                res["poliza"] = {
                    "cuadrada": poliza_res.cuadrada,
                    "diferencia": float(poliza_res.diferencia),
                    "encabezado": {
                        "fecha": mov_copia["fecha"].isoformat() if hasattr(mov_copia["fecha"], "isoformat") else str(mov_copia["fecha"]),
                        "tipo": plantilla["tipo_poliza"],
                        "concepto": plantilla["concepto_poliza"],
                    },
                    "asientos": [
                        {
                            "cuenta": a.cuenta,
                            "naturaleza": a.naturaleza,
                            "tipo_movimiento": a.naturaleza,
                            "importe": float(a.importe),
                            "descripcion": a.descripcion,
                            "concepto": a.descripcion,
                        } for a in poliza_res.lineas
                    ],
                }
            else:
                res["poliza"] = None
                res["error"] = "Plantilla no encontrada para la regla"
                
        resultados.append(res)
        
    return jsonify({
        "status": "ok",
        "procesados": len(resultados),
        "resultados": resultados
    })

