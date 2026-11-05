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
Módulo de aprendizaje para Orange Poliza Engine.

Cuando el motor de reglas (rule_engine.py) no encuentra ninguna regla
para un movimiento, se le pregunta al usuario. Este módulo toma esa
respuesta y decide CÓMO convertirla en una regla reutilizable, para que
la próxima vez que aparezca "lo mismo" el sistema ya no pregunte.

Importante: esto NO es una IA que decide sola. Es un sistema de
aprendizaje supervisado: el usuario siempre confirma o ajusta la
sugerencia antes de guardarla. La función principal,
`sugerir_regla_desde_clasificacion_manual`, propone la regla; quien la
guarda de verdad es la capa de API (más adelante), después de que el
usuario le da "Aceptar".
"""

from dataclasses import dataclass, field
from typing import Optional
import re

from rule_engine import normalizar_texto

_PATRON_CUENTA_TRAS_SLASH = re.compile(r"/\s*([A-Z0-9]{6,})", re.IGNORECASE)

def _detectar_cuenta_en_texto(descripcion: str) -> str:
    """Busca un número de cuenta/referencia largo después de un '/' en
    la descripción cruda (antes de normalizar) — común en SPEI y pagos
    a terceros ('SPEI ENVIADO BANORTE/0028920544', 'PAGO CUENTA DE
    TERCERO/ 0031797026'). Ese número identifica al destinatario real
    mucho mejor que las palabras alrededor ('ENVIADO', 'CUENTA', 'SPEI'),
    que son genéricas y aparecen en decenas de movimientos distintos que
    en realidad van a lugares diferentes."""
    m = _PATRON_CUENTA_TRAS_SLASH.search(descripcion)
    if not m:
        return ""
    candidato = m.group(1)
    if sum(c.isdigit() for c in candidato) >= 6:
        return candidato
    return ""

@dataclass
class SugerenciaRegla:
    """Lo que se le muestra al usuario para confirmar antes de guardar."""
    nombre_sugerido: str
    tipo_coincidencia: str          # 'rfc' | 'cuenta_bancaria' | 'palabra_clave' | 'exacta'
    valor_coincidencia: str          # el RFC, cuenta o palabra clave detectada
    tipo_movimiento: Optional[str]
    explicacion: str
    alternativas: list = field(default_factory=list)  # otras palabras clave candidatas
    palabra_clave_fallback: str = ""
    descripcion_exacta_fallback: str = ""

_BANCOS_CONOCIDOS = {
    "BBVA", "BANCOMER", "SANTANDER", "BANAMEX", "CITIBANAMEX", "BANORTE",
    "HSBC", "SCOTIABANK", "INBURSA", "BAJIO", "AZTECA", "BANCO AZTECA",
    "STP", "OXXO",
}

def _limpiar_token(palabra: str) -> str:
    return palabra.strip(",:;.()[]")

def _extraer_palabra_clave_candidata(descripcion_normalizada: str) -> str:
    """
    De una descripción normalizada tipo "COMPRA OXXO SUC 332", intenta
    encontrar la(s) palabra(s) más "identificadora(s)" del comercio/
    concepto: normalmente lo que sobra después de quitar verbos y
    términos genéricos de banco (COMPRA, PAGO, SPEI, ENVIADO, CUENTA,
    TERCERO, GUIA...). Si sobran 2+ palabras "fuertes" seguidas, se usan
    las dos juntas (ej. "CLAVOS NACIONALES") en vez de solo la primera:
    una sola palabra genérica-ish ("CLAVOS") es más fácil que coincida
    por accidente con otro movimiento no relacionado que la frase
    completa.
    """
    palabras_genericas = {
        "COMPRA", "PAGO", "PAGOS", "TRANSFERENCIA", "TRANSF", "DEPOSITO",
        "RETIRO", "SPEI", "ABONO", "CARGO", "ENVIO", "ENVIADO", "RECIBIDO",
        "RECEPCION", "RECIBO", "DE", "A", "EL", "LA", "LOS", "LAS", "PARA",
        "CON", "SUC", "SUCURSAL", "CUENTA", "CTA", "TERCERO", "TERCEROS",
        "GUIA", "NO", "NUM", "NUMERO", "FOLIO", "REF", "REFERENCIA",
        "CLABE", "SA", "CV", "RL",
    }
    palabras = [_limpiar_token(p) for p in descripcion_normalizada.replace(",", " ").split()]
    palabras = [p for p in palabras if p]
    candidatas = [
        p for p in palabras
        if p not in palabras_genericas and len(p) >= 3 and not p.isdigit()
    ]
    return " ".join(candidatas[:2]) if candidatas else ""

def sugerir_regla_desde_clasificacion_manual(movimiento: dict, rfc_empresa: str = None) -> SugerenciaRegla:
    """
    Analiza un movimiento que el usuario acaba de clasificar a mano y
    sugiere la mejor forma de convertirlo en regla, en orden de
    preferencia: RFC > cuenta bancaria > palabra clave de la descripción.

    Esto se le muestra al usuario en una ventana de confirmación como:

        "Detectamos el RFC ABC010101XXX. ¿Quieres que la próxima vez
         que llegue un movimiento de este RFC se clasifique
         automáticamente igual? [Sí, crear regla] [No, preguntar cada vez]"
    """
    descripcion = movimiento.get("descripcion", "")
    descripcion_norm = normalizar_texto(descripcion)
    tipo_mov = movimiento.get("tipo")
    rfc = (movimiento.get("rfc_contraparte") or "").strip()
    if rfc.upper() in {"ND", "N/A", "NA", "N.D.", "N.A.", "XAXX010101000", "XEXX010101000", "000000000000", "XXX"}:
        rfc = ""
    cuenta_bancaria = (movimiento.get("cuenta_bancaria_contraparte") or "").strip()

    if rfc and rfc_empresa and rfc.upper() == rfc_empresa.upper():
        rfc = ""

    palabra_clave = _extraer_palabra_clave_candidata(descripcion_norm)
        
    es_exacta = False
    if not palabra_clave or len(palabra_clave) < 3:
        es_exacta = True

    if rfc:
        return SugerenciaRegla(
            nombre_sugerido=f"Movimientos de {rfc}",
            tipo_coincidencia="rfc",
            valor_coincidencia=rfc,
            tipo_movimiento=tipo_mov,
            palabra_clave_fallback="" if es_exacta else palabra_clave,
            descripcion_exacta_fallback=descripcion.strip() if es_exacta else "",
            explicacion=(
                f"Este movimiento trae el RFC {rfc}. Es la forma más confiable "
                f"de identificar al mismo proveedor/cliente en el futuro, "
                f"aunque cambie el texto de la descripción."
            ),
        )

    if cuenta_bancaria:
        return SugerenciaRegla(
            nombre_sugerido=f"Movimientos de cuenta {cuenta_bancaria}",
            tipo_coincidencia="cuenta_bancaria",
            valor_coincidencia=cuenta_bancaria,
            tipo_movimiento=tipo_mov,
            palabra_clave_fallback="" if es_exacta else palabra_clave,
            descripcion_exacta_fallback=descripcion.strip() if es_exacta else "",
            explicacion=(
                f"Este movimiento viene de la cuenta bancaria {cuenta_bancaria}. "
                f"Se puede usar para identificar siempre al mismo origen/destino."
            ),
        )

    palabra_clave = _extraer_palabra_clave_candidata(descripcion_norm)
    palabra_es_debil = (
        not palabra_clave
        or len(palabra_clave) < 4
        or palabra_clave.upper() in _BANCOS_CONOCIDOS
    )

    cuenta_detectada = _detectar_cuenta_en_texto(descripcion) if (not palabra_clave or len(palabra_clave) < 4) else ""
    if cuenta_detectada:
        return SugerenciaRegla(
            nombre_sugerido=f"Movimientos a la cuenta {cuenta_detectada}",
            tipo_coincidencia="cuenta_bancaria",
            valor_coincidencia=cuenta_detectada,
            tipo_movimiento=tipo_mov,
            palabra_clave_fallback="" if es_exacta else palabra_clave,
            descripcion_exacta_fallback=descripcion.strip() if es_exacta else "",
            explicacion=(
                f"No encontramos una palabra confiable para identificar "
                f"este movimiento. La descripción trae el número "
                f"{cuenta_detectada}, que sí identifica al destinatario "
                f"real; se usa ese en su lugar."
            ),
        )

    if not palabra_clave or len(palabra_clave) < 3:
        return SugerenciaRegla(
            nombre_sugerido="Regla exacta para este movimiento",
            tipo_coincidencia="exacta",
            valor_coincidencia=descripcion.strip(),
            tipo_movimiento=tipo_mov,
            explicacion=(
                "No encontramos ninguna palabra ni número realmente identificador "
                "aquí, por lo que una regla difusa sería demasiado arriesgada. "
                "Esta regla solo se aplicará si el movimiento tiene exactamente "
                f"la misma descripción: '{descripcion}'."
            ),
        )

    alternativas = [
        p for p in descripcion_norm.split()
        if p != palabra_clave and len(p) >= 3
    ][:4]

    return SugerenciaRegla(
        nombre_sugerido=f"Movimientos con '{palabra_clave}'",
        tipo_coincidencia="palabra_clave",
        valor_coincidencia=palabra_clave,
        tipo_movimiento=tipo_mov,
        explicacion=(
            f"No venía RFC ni cuenta bancaria en este movimiento. Detectamos "
            f"'{palabra_clave}' como la palabra más identificadora de la "
            f"descripción ('{descripcion_norm}'). Cualquier movimiento futuro "
            f"cuya descripción contenga esa palabra se clasificará igual. "
            f"Puedes usar otra palabra si esta no es la correcta."
        ),
        alternativas=alternativas,
    )

def construir_regla_a_partir_de_sugerencia(
    sugerencia: SugerenciaRegla,
    empresa_id: int,
    plantilla_movimientos: list,
    usuario_id: Optional[int] = None,
    prioridad: int = 100,
) -> dict:
    """
    Convierte una SugerenciaRegla ya confirmada por el usuario (posiblemente
    con la palabra clave editada) en el dict listo para INSERT en la tabla
    `reglas` + `plantilla_movimientos` + `regla_palabras_clave`.

    La capa de API es responsable de hacer los INSERTs reales; esta función
    solo arma la estructura para no repetir esta lógica en cada endpoint.
    """
    regla = {
        "empresa_id": empresa_id,
        "nombre": sugerencia.nombre_sugerido,
        "prioridad": prioridad,
        "activa": True,
        "tipo_movimiento": sugerencia.tipo_movimiento,
        "creada_por": usuario_id,
        "rfc_contraparte": None,
        "cuenta_bancaria_contraparte": None,
        "descripcion_exacta": None,
        "palabras_clave": [],
        "plantilla_movimientos": plantilla_movimientos,
    }

    if sugerencia.tipo_coincidencia == "rfc":
        regla["rfc_contraparte"] = sugerencia.valor_coincidencia
    elif sugerencia.tipo_coincidencia == "cuenta_bancaria":
        regla["cuenta_bancaria_contraparte"] = sugerencia.valor_coincidencia
    elif sugerencia.tipo_coincidencia == "palabra_clave":
        regla["palabras_clave"] = [sugerencia.valor_coincidencia]
    elif sugerencia.tipo_coincidencia == "exacta":
        regla["descripcion_exacta"] = sugerencia.valor_coincidencia

    return regla

