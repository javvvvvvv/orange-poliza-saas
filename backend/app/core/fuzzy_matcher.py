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
"""Motor de coincidencia difusa (fuzzy matching) de último recurso;
ver `rule_engine.py` para dónde entra en el orden de prioridad."""

import base64
import difflib
import json
import os
from dataclasses import dataclass

_DIR = os.path.dirname(__file__)
_LLAVE = b"OC-9k-reglas-2026"

def _cargar_mapa() -> dict:
    with open(os.path.join(_DIR, "_k9d2.dat")) as f:
        blob = f.read().strip()
    xored = base64.b85decode(blob)
    raw = bytes(b ^ _LLAVE[i % len(_LLAVE)] for i, b in enumerate(xored))
    return json.loads(raw)["fz"]

_CFG = _cargar_mapa()
STOPWORDS = set(_CFG["sw"])

def _tokens(texto: str) -> set:
    return set(t for t in texto.split() if len(t) >= 3 and t not in STOPWORDS)

def similitud_caracteres(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()

def similitud_tokens(a: str, b: str) -> float:
    tokens_a, tokens_b = _tokens(a), _tokens(b)
    if not tokens_a or not tokens_b:
        return 0.0
    interseccion = len(tokens_a & tokens_b)
    union = len(tokens_a | tokens_b)
    return interseccion / union if union else 0.0

def similitud_abreviatura(palabra_corta: str, palabra_larga: str) -> float:
    if not palabra_corta or not palabra_larga:
        return 0.0
    if len(palabra_corta) > len(palabra_larga):
        palabra_corta, palabra_larga = palabra_larga, palabra_corta
    if palabra_larga.startswith(palabra_corta):
        return len(palabra_corta) / len(palabra_larga)
    return 0.0

def _mejor_score_para_token(token_objetivo: str, tokens_candidatos: list) -> float:
    mejor = 0.0
    for token in tokens_candidatos:
        score = max(
            similitud_abreviatura(token, token_objetivo),
            difflib.SequenceMatcher(None, token, token_objetivo).ratio(),
        )
        mejor = max(mejor, score)
    return mejor

def similitud_combinada(descripcion: str, palabra_clave: str) -> float:
    descripcion = descripcion.strip()
    palabra_clave = palabra_clave.strip()
    if not descripcion or not palabra_clave:
        return 0.0
    if descripcion == palabra_clave:
        return 1.0

    tokens_desc = list(_tokens(descripcion))
    tokens_clave = list(_tokens(palabra_clave))

    if not tokens_clave or not tokens_desc:
        return similitud_caracteres(descripcion, palabra_clave)

    scores = [_mejor_score_para_token(t, tokens_desc) for t in tokens_clave]
    
    # Todos los tokens de la palabra clave deben estar presentes con al menos un 70% de similitud.
    # Si un token de la regla falta por completo (ej: ELISA MARIA vs MARIA GUADALUPE),
    # no deberia hacer match.
    if any(s < 0.70 for s in scores):
        return 0.0
        
    return round(sum(scores) / len(scores), 4)

@dataclass
class CoincidenciaDifusa:
    texto_comparado: str
    similitud: float
    fuente: str

def buscar_mejor_coincidencia_difusa(
    descripcion_normalizada: str,
    candidatos: list,
    umbral_minimo: float = _CFG["um"],
) -> CoincidenciaDifusa | None:
    mejor = None
    for texto, fuente in candidatos:
        score = similitud_combinada(descripcion_normalizada, texto)
        if score >= umbral_minimo and (mejor is None or score > mejor.similitud):
            mejor = CoincidenciaDifusa(texto_comparado=texto, similitud=score, fuente=fuente)
    return mejor

def confianza_desde_similitud(similitud: float) -> int:
    a, b = _CFG["cf"]
    return int(round(a + similitud * b))

