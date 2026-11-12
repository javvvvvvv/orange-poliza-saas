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
"""Motor de reglas de Orange Poliza Engine: dado un movimiento y las
reglas activas de una empresa, decide qué plantilla contable aplica.
Orden de prioridad: RFC exacto > cuenta bancaria exacta > descripción
exacta > contiene palabra clave > similitud difusa > sin coincidencia."""

import base64
import json
import os
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Optional

from ml_engine import PredictorML
from fuzzy_matcher import similitud_combinada, confianza_desde_similitud, BANK_STOPWORDS

_DIR = os.path.dirname(__file__)
_LLAVE = b"OC-9k-reglas-2026"

def _cargar_mapa() -> dict:
    with open(os.path.join(_DIR, "_k9d2.dat")) as f:
        blob = f.read().strip()
    xored = base64.b85decode(blob)
    raw = bytes(b ^ _LLAVE[i % len(_LLAVE)] for i, b in enumerate(xored))
    return json.loads(raw)["re"]

_CFG = _cargar_mapa()
UMBRAL_DIFUSO = max(_CFG["um"], 0.80)

def normalizar_texto(texto: str) -> str:
    if not texto:
        return ""

    texto = texto.upper().strip()
    texto = unicodedata.normalize("NFKD", texto)
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    texto = re.sub(r"\b\d{4,}\b", "", texto)
    texto = re.sub(r"[\-_/\\]+", " ", texto)
    texto = re.sub(r"\s+", " ", texto)

    return texto.strip()

@dataclass
class Regla:
    id: int
    empresa_id: int
    nombre: str
    prioridad: int
    activa: bool

    # Condiciones (cualquiera que esté presente se evalúa; todas las
    # presentes deben cumplirse para que la regla aplique)
    rfc_contraparte: Optional[str] = None
    cuenta_bancaria_contraparte: Optional[str] = None
    descripcion_exacta: Optional[str] = None       # comparación normalizada exacta
    descripcion_contiene: list = field(default_factory=list)  # lista de palabras clave
    tipo_movimiento: Optional[str] = None           # 'ingreso' | 'egreso' | None (cualquiera)

    # Qué generar si la regla aplica
    plantilla_id: int = None

@dataclass
class MatchResult:
    """Resultado de tratar de encontrar una regla para un movimiento."""
    regla: Optional[Regla]
    confianza: int  # 0-100
    motivo: str      # explicación humana de por qué (o por qué no) hizo match
    nivel: str        # 'rfc' | 'cuenta_bancaria' | 'exacta' | 'contiene' | 'sin_coincidencia'

def distancia_levenshtein(s1: str, s2: str) -> int:
    """Calcula la distancia de edición (Levenshtein) entre dos cadenas."""
    if s1 == s2:
        return 0
    if len(s1) < len(s2):
        return distancia_levenshtein(s2, s1)
    if len(s2) == 0:
        return len(s1)
    prev = list(range(len(s2) + 1))
    for i, c1 in enumerate(s1):
        curr = [i + 1]
        for j, c2 in enumerate(s2):
            curr.append(min(prev[j + 1] + 1, curr[j] + 1, prev[j] + (c1 != c2)))
        prev = curr
    return prev[-1]

def rfcs_son_equivalentes(rfc1: str, rfc2: str, max_dist: int = 2) -> bool:
    """
    Determina si dos RFCs son idénticos o equivalentes tolerando errores tipográficos menores
    o variaciones en fecha/homoclave bancaria siempre que compartan el prefijo alfabético de la razón social.
    """
    if not rfc1 or not rfc2:
        return False
    r1 = rfc1.replace("-", "").replace(" ", "").strip().upper()
    r2 = rfc2.replace("-", "").replace(" ", "").strip().upper()
    if r1 == r2:
        return True

    genericos = {"ND", "N/A", "NA", "N.D.", "N.A.", "XAXX010101000", "XEXX010101000", "000000000000", "XXX"}
    if r1 in genericos or r2 in genericos:
        return False

    if not (10 <= len(r1) <= 14 and 10 <= len(r2) <= 14):
        return False

    prefix_len = 3 if (len(r1) < 13 or len(r2) < 13) else 4
    if r1[:prefix_len] != r2[:prefix_len]:
        return False

    # Si comparten el prefijo de la razón social y ambos tienen longitud típica de RFC (11-13 caracteres),
    # representan la misma entidad (tolerando discrepancias en fecha de registro o homoclave bancaria)
    if len(r1) in (11, 12, 13) and len(r2) in (11, 12, 13):
        return True

    return distancia_levenshtein(r1, r2) <= max_dist

def _es_compatible_tercero(regla: Regla, rfc_mov: str, cuenta_mov: str) -> bool:
    """Verifica si la regla no contradice el RFC o cuenta bancaria del movimiento."""
    if regla.rfc_contraparte and rfc_mov:
        if not rfcs_son_equivalentes(regla.rfc_contraparte, rfc_mov):
            return False
    if regla.cuenta_bancaria_contraparte and cuenta_mov:
        if regla.cuenta_bancaria_contraparte.strip() != cuenta_mov:
            return False
    return True

def encontrar_regla(movimiento: dict, reglas: list[Regla]) -> MatchResult:
    descripcion_norm = normalizar_texto(movimiento.get("descripcion", ""))
    tipo_mov = movimiento.get("tipo")
    rfc_mov = (movimiento.get("rfc_contraparte") or "").strip().upper()
    if rfc_mov in {"ND", "N/A", "NA", "N.D.", "N.A.", "XAXX010101000", "XEXX010101000", "000000000000", "XXX"}:
        rfc_mov = ""
    cuenta_mov = (movimiento.get("cuenta_bancaria_contraparte") or "").strip()

    reglas_activas = [r for r in reglas if r.activa]
    reglas_ordenadas = sorted(reglas_activas, key=lambda r: r.prioridad)

    # --- Nivel 1: RFC exacto o equivalente (tolera error tipográfico menor) ---
    for regla in reglas_ordenadas:
        if regla.rfc_contraparte and rfc_mov and rfcs_son_equivalentes(regla.rfc_contraparte, rfc_mov):
            if regla.tipo_movimiento and regla.tipo_movimiento != tipo_mov:
                continue
            es_exacto = (regla.rfc_contraparte.upper() == rfc_mov)
            confianza = 100 if es_exacto else 98
            motivo = (
                f"El RFC del contraparte ({rfc_mov}) coincide exactamente con la regla '{regla.nombre}'."
                if es_exacto else
                f"El RFC del contraparte ({rfc_mov}) coincide con la regla '{regla.nombre}' ({regla.rfc_contraparte}) con tolerancia de error de dedo."
            )
            return MatchResult(
                regla=regla,
                confianza=confianza,
                motivo=motivo,
                nivel="rfc",
            )

    # --- Nivel 2: cuenta bancaria exacta ---
    for regla in reglas_ordenadas:
        if not _es_compatible_tercero(regla, rfc_mov, cuenta_mov):
            continue
        if (regla.cuenta_bancaria_contraparte and cuenta_mov
                and regla.cuenta_bancaria_contraparte == cuenta_mov):
            if regla.tipo_movimiento and regla.tipo_movimiento != tipo_mov:
                continue
            return MatchResult(
                regla=regla,
                confianza=100,
                motivo=f"La cuenta bancaria contraparte ({cuenta_mov}) coincide "
                       f"exactamente con la regla '{regla.nombre}'.",
                nivel="cuenta_bancaria",
            )

    # --- Nivel 3: descripcion exacta ---
    for regla in reglas_ordenadas:
        if not _es_compatible_tercero(regla, rfc_mov, cuenta_mov):
            continue
        if regla.descripcion_exacta:
            desc_original = movimiento.get("descripcion", "")
            if regla.descripcion_exacta.strip().upper() == desc_original.strip().upper():
                if regla.tipo_movimiento and regla.tipo_movimiento != tipo_mov:
                    continue
                return MatchResult(
                    regla=regla,
                    confianza=100,
                    motivo=f"La descripción ('{desc_original}') "
                           f"coincide exactamente con la regla '{regla.nombre}'.",
                    nivel="exacta",
                )

    # --- Nivel 4: descripción contiene alguna palabra clave ---
    mejor_match = None
    mejor_confianza = -1
    for regla in reglas_ordenadas:
        if not _es_compatible_tercero(regla, rfc_mov, cuenta_mov):
            continue
        if regla.tipo_movimiento and regla.tipo_movimiento != tipo_mov:
            continue
        for palabra_clave in regla.descripcion_contiene:
            palabra_norm = normalizar_texto(palabra_clave)
            if not palabra_norm or len(palabra_norm) < 3:
                continue
            if palabra_norm in BANK_STOPWORDS:
                continue
            # Coincidencia con límites de palabra para evitar subcadenas falsas (ej. 'SOL' en 'RESOLUCION')
            patron = rf"\b{re.escape(palabra_norm)}\b"
            if re.search(patron, descripcion_norm):
                # Palabras clave largas o compuestas (>= 8 letras o frases) tienen mayor especificidad
                if len(palabra_norm) >= 8 or " " in palabra_norm:
                    confianza = 95
                else:
                    confianza = min(90, 75 + len(palabra_norm) * 2)

                # Si el movimiento tiene un RFC conocido pero la regla no especifica RFC,
                # se reduce la confianza para sugerir y evitar clasificaciones erróneas
                if rfc_mov and not regla.rfc_contraparte:
                    confianza = min(confianza, 80)

                if confianza > mejor_confianza:
                    mejor_confianza = confianza
                    mejor_match = MatchResult(
                        regla=regla,
                        confianza=confianza,
                        motivo=f"La descripción contiene la palabra clave "
                               f"'{palabra_clave}' configurada en la regla "
                               f"'{regla.nombre}'.",
                        nivel="contiene",
                    )

    if mejor_match:
        return mejor_match

    # --- Nivel 5: coincidencia difusa ---
    candidatos = []
    for regla in reglas_ordenadas:
        if not _es_compatible_tercero(regla, rfc_mov, cuenta_mov):
            continue
        if regla.tipo_movimiento and regla.tipo_movimiento != tipo_mov:
            continue
        for palabra_clave in regla.descripcion_contiene:
            palabra_norm = normalizar_texto(palabra_clave)
            if palabra_norm and len(palabra_norm) >= 3 and palabra_norm not in BANK_STOPWORDS:
                candidatos.append((palabra_norm, regla))

    if candidatos:
        mejor_score = -1.0
        mejor_regla_difusa = None
        for texto_candidato, regla in candidatos:
            score = similitud_combinada(descripcion_norm, texto_candidato)
            if score > mejor_score:
                mejor_score = score
                mejor_regla_difusa = regla

        if mejor_score >= UMBRAL_DIFUSO and mejor_regla_difusa is not None:
            confianza_dif = confianza_desde_similitud(mejor_score)
            if rfc_mov and not mejor_regla_difusa.rfc_contraparte:
                confianza_dif = min(confianza_dif, 80)
            return MatchResult(
                regla=mejor_regla_difusa,
                confianza=confianza_dif,
                motivo=(
                    f"La descripción '{descripcion_norm}' se parece "
                    f"({round(mejor_score * 100)}% de similitud, considerando "
                    f"abreviaturas y orden distinto de palabras) a lo que ya "
                    f"reconoce la regla '{mejor_regla_difusa.nombre}'."
                ),
                nivel="difusa",
            )

    # --- Sin coincidencia ---
    return MatchResult(
        regla=None,
        confianza=0,
        motivo="Ningún RFC, cuenta bancaria, descripción exacta, palabra clave "
               "ni similitud difusa coincide con este movimiento. Se necesita "
               "clasificación manual para crear una regla nueva.",
        nivel="sin_coincidencia",
    )

