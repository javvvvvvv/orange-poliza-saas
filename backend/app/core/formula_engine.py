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
Motor de fórmulas para Orange Poliza Engine (con aritmética exacta Decimal).

Permite que cada empresa defina, por regla, cómo se calcula el importe
de cada movimiento de la póliza a partir de variables conocidas del
movimiento original (TOTAL, TASA_IVA, RET_IVA, etc.), usando una
expresión de texto tipo:

    "TOTAL / 1.16"
    "TOTAL * 0.16"
    "BASE - RET_IVA"
    "TOTAL - (TOTAL / (1 + TASA_IVA))"

No usamos eval() directo sobre el texto del usuario porque eso permitiría
ejecutar cualquier código Python. En vez de eso, parseamos la expresión
con el módulo `ast` y solo permitimos un subconjunto muy limitado de
nodos (números, nombres de variables conocidas, +, -, *, /, paréntesis,
signo negativo y la función round()). Cualquier otra cosa (imports,
llamadas a funciones no permitidas, atributos, etc.) se rechaza.
"""

import ast
import operator
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation

class FormulaError(Exception):
    """Se lanza cuando una fórmula es inválida o usa algo no permitido."""
    pass

class MontoDecimal(Decimal):
    """Subclase de `decimal.Decimal` que preserva precisión decimal exacta
    y además convierte operandos `float` vía `Decimal(str(x))` si código
    externo mezcla `Decimal` con `float`, evitando `TypeError`."""

    @staticmethod
    def _coercionar(otro):
        if isinstance(otro, Decimal):
            return otro
        if isinstance(otro, float):
            return Decimal(str(otro))
        return otro

    def __add__(self, otro):
        res = super().__add__(self._coercionar(otro))
        return MontoDecimal(res) if isinstance(res, Decimal) else res

    def __radd__(self, otro):
        res = super().__radd__(self._coercionar(otro))
        return MontoDecimal(res) if isinstance(res, Decimal) else res

    def __sub__(self, otro):
        res = super().__sub__(self._coercionar(otro))
        return MontoDecimal(res) if isinstance(res, Decimal) else res

    def __rsub__(self, otro):
        res = super().__rsub__(self._coercionar(otro))
        return MontoDecimal(res) if isinstance(res, Decimal) else res

    def __mul__(self, otro):
        res = super().__mul__(self._coercionar(otro))
        return MontoDecimal(res) if isinstance(res, Decimal) else res

    def __rmul__(self, otro):
        res = super().__rmul__(self._coercionar(otro))
        return MontoDecimal(res) if isinstance(res, Decimal) else res

    def __truediv__(self, otro):
        res = super().__truediv__(self._coercionar(otro))
        return MontoDecimal(res) if isinstance(res, Decimal) else res

    def __rtruediv__(self, otro):
        res = super().__rtruediv__(self._coercionar(otro))
        return MontoDecimal(res) if isinstance(res, Decimal) else res

    def __abs__(self):
        return MontoDecimal(super().__abs__())

    def __neg__(self):
        return MontoDecimal(super().__neg__())

    def __pos__(self):
        return MontoDecimal(super().__pos__())

    def __round__(self, n=None):
        if n is None:
            return int(self.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        n_int = int(n)
        escala = Decimal("1") if n_int <= 0 else Decimal("1." + ("0" * n_int))
        return MontoDecimal(self.quantize(escala, rounding=ROUND_HALF_UP))

_DOS_DECIMALES = Decimal("0.01")

def a_decimal(valor, default: str = "0.00") -> MontoDecimal:
    """Convierte cualquier valor numérico (`Decimal`, `int`, `float`, `str`)
    a `MontoDecimal` sin pérdida de precisión binaria."""
    if isinstance(valor, MontoDecimal):
        return valor
    if isinstance(valor, Decimal):
        return MontoDecimal(valor)
    if valor is None or valor == "":
        return MontoDecimal(default)
    try:
        return MontoDecimal(str(valor))
    except (InvalidOperation, ValueError, TypeError) as e:
        raise FormulaError(f"Valor numérico inválido: {valor!r}") from e

def redondear_moneda(valor) -> MontoDecimal:
    """Redondea un importe monetario a 2 decimales con ROUND_HALF_UP."""
    return MontoDecimal(a_decimal(valor).quantize(_DOS_DECIMALES, rounding=ROUND_HALF_UP))

def _fn_round(valor, ndigits=2) -> MontoDecimal:
    d = a_decimal(valor)
    n_int = int(ndigits)
    escala = Decimal("1") if n_int <= 0 else Decimal("1." + ("0" * n_int))
    return MontoDecimal(d.quantize(escala, rounding=ROUND_HALF_UP))

# Operadores aritméticos permitidos
_OPERADORES_BINARIOS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}

_OPERADORES_UNARIOS = {
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}

# Funciones permitidas dentro de una fórmula
_FUNCIONES_PERMITIDAS = {
    "round": _fn_round,
    "abs": abs,
    "min": min,
    "max": max,
}

# Variables que el motor de reglas expone a cada fórmula.
# Cada movimiento normalizado se traduce a este diccionario antes de evaluar.
VARIABLES_DISPONIBLES = [
    "TOTAL",        # importe total del movimiento (siempre positivo)
    "TASA_IVA",     # ej. 0.16
    "BASE",         # TOTAL / (1 + TASA_IVA), calculado automáticamente si hay IVA
    "SUBTOTAL",     # Alias contable para BASE
    "IVA",          # TOTAL - BASE, calculado automáticamente si hay IVA
    "RET_IVA",      # retención de IVA capturada manualmente (0 si no aplica)
    "RET_ISR",      # retención de ISR capturada manualmente (0 si no aplica)
    "TIPO_CAMBIO",  # tipo de cambio si el movimiento es en dólares (1.0 si es en pesos)
]

def evaluar_formula(expresion: str, variables: dict) -> MontoDecimal:
    """
    Evalúa una fórmula de texto de forma segura usando `Decimal` y regresa
    el resultado redondeado a 2 decimales (ROUND_HALF_UP).

    :param expresion: texto de la fórmula, ej. "TOTAL / 1.16"
    :param variables: diccionario con los valores actuales del movimiento,
                       ej. {"TOTAL": Decimal("11600.00"), "TASA_IVA": Decimal("0.16"), ...}
    :raises FormulaError: si la fórmula no es válida o usa algo no permitido
    """
    if not expresion or not expresion.strip():
        raise FormulaError("La fórmula está vacía.")

    try:
        arbol = ast.parse(expresion, mode="eval")
    except SyntaxError as e:
        raise FormulaError(f"Fórmula con error de sintaxis: {expresion!r} ({e})")

    resultado = _evaluar_nodo(arbol.body, variables)

    if isinstance(resultado, bool) or not isinstance(resultado, (int, float, Decimal)):
        raise FormulaError(f"La fórmula '{expresion}' no regresó un número.")

    return redondear_moneda(resultado)

def _evaluar_nodo(nodo, variables: dict):
    # Número literal: 1.16, 100, etc.
    if isinstance(nodo, ast.Constant):
        if isinstance(nodo.value, bool) or not isinstance(nodo.value, (int, float, Decimal)):
            raise FormulaError(f"Valor no permitido en la fórmula: {nodo.value!r}")
        return a_decimal(nodo.value)

    # Nombre de variable: TOTAL, BASE, IVA, etc.
    if isinstance(nodo, ast.Name):
        nombre = nodo.id
        if nombre not in variables:
            disponibles = ", ".join(sorted(variables.keys()))
            raise FormulaError(
                f"La variable '{nombre}' no está disponible. "
                f"Variables disponibles: {disponibles}"
            )
        return a_decimal(variables[nombre])

    # Operación binaria: a + b, a - b, a * b, a / b
    if isinstance(nodo, ast.BinOp):
        tipo_op = type(nodo.op)
        if tipo_op not in _OPERADORES_BINARIOS:
            raise FormulaError(f"Operador no permitido: {tipo_op.__name__}")
        izquierda = _evaluar_nodo(nodo.left, variables)
        derecha = _evaluar_nodo(nodo.right, variables)
        if tipo_op is ast.Div and derecha == 0:
            raise FormulaError("División entre cero en la fórmula.")
        return _OPERADORES_BINARIOS[tipo_op](a_decimal(izquierda), a_decimal(derecha))

    # Operación unaria: -TOTAL, +TOTAL
    if isinstance(nodo, ast.UnaryOp):
        tipo_op = type(nodo.op)
        if tipo_op not in _OPERADORES_UNARIOS:
            raise FormulaError(f"Operador unario no permitido: {tipo_op.__name__}")
        valor = _evaluar_nodo(nodo.operand, variables)
        return _OPERADORES_UNARIOS[tipo_op](a_decimal(valor))

    # Llamada a función: round(TOTAL / 1.16, 2)
    if isinstance(nodo, ast.Call):
        if not isinstance(nodo.func, ast.Name):
            raise FormulaError("Solo se permiten llamadas a funciones simples.")
        nombre_funcion = nodo.func.id
        if nombre_funcion not in _FUNCIONES_PERMITIDAS:
            raise FormulaError(f"Función no permitida: {nombre_funcion}()")
        argumentos = [_evaluar_nodo(a, variables) for a in nodo.args]
        return _FUNCIONES_PERMITIDAS[nombre_funcion](*argumentos)

    raise FormulaError(f"Elemento no permitido en la fórmula: {type(nodo).__name__}")

def construir_variables(
    total,
    tasa_iva=Decimal("0.16"),
    tiene_iva: bool = True,
    ret_iva=Decimal("0.00"),
    ret_isr=Decimal("0.00"),
    tipo_cambio=Decimal("1.0"),
    tasa_ret_iva=Decimal("0.0"),
    tasa_ret_isr=Decimal("0.0"),
    iva_exacto=None,
) -> dict[str, MontoDecimal]:
    """
    Construye el diccionario de variables `Decimal` que se le pasa a evaluar_formula(),
    a partir de los datos de un movimiento ya normalizado.
    """
    total_d = redondear_moneda(total)
    tasa_iva_d = a_decimal(tasa_iva, "0.16")
    tasa_ret_iva_d = a_decimal(tasa_ret_iva, "0.0")
    tasa_ret_isr_d = a_decimal(tasa_ret_isr, "0.0")
    ret_iva_d = redondear_moneda(ret_iva)
    ret_isr_d = redondear_moneda(ret_isr)
    tipo_cambio_d = a_decimal(tipo_cambio, "1.0")

    if tiene_iva:
        divisor = Decimal("1") + tasa_iva_d - tasa_ret_iva_d - tasa_ret_isr_d
        tiene_tasas_ret = (tasa_ret_iva_d > 0 or tasa_ret_isr_d > 0)
        if iva_exacto is not None:
            iva_d = redondear_moneda(iva_exacto)
            base_prelim = redondear_moneda(total_d / divisor) if divisor != 0 else (total_d - iva_d)
            if ret_iva_d == Decimal("0.00") and tasa_ret_iva_d > 0:
                ret_iva_d = redondear_moneda(base_prelim * tasa_ret_iva_d)
            if ret_isr_d == Decimal("0.00") and tasa_ret_isr_d > 0:
                ret_isr_d = redondear_moneda(base_prelim * tasa_ret_isr_d)
            if tiene_tasas_ret:
                base_d = redondear_moneda(total_d - iva_d + ret_iva_d + ret_isr_d)
            else:
                base_d = redondear_moneda(total_d - iva_d)
        else:
            base_d = redondear_moneda(total_d / divisor) if divisor != 0 else total_d
            if ret_iva_d == Decimal("0.00") and tasa_ret_iva_d > 0:
                ret_iva_d = redondear_moneda(base_d * tasa_ret_iva_d)
            if ret_isr_d == Decimal("0.00") and tasa_ret_isr_d > 0:
                ret_isr_d = redondear_moneda(base_d * tasa_ret_isr_d)
            if tiene_tasas_ret:
                iva_d = redondear_moneda(total_d - base_d + ret_iva_d + ret_isr_d)
            else:
                iva_d = redondear_moneda(total_d - base_d)
    else:
        base_d = total_d
        iva_d = MontoDecimal("0.00")

    return {
        "TOTAL": total_d,
        "TASA_IVA": tasa_iva_d,
        "BASE": base_d,
        "SUBTOTAL": base_d,
        "IVA": iva_d,
        "RET_IVA": ret_iva_d,
        "RET_ISR": ret_isr_d,
        "TIPO_CAMBIO": tipo_cambio_d,
    }

def validar_formula(expresion: str) -> bool:
    """
    Valida que una fórmula sea sintácticamente correcta y solo use
    variables conocidas, sin necesidad de tener valores reales todavía.
    Útil para validar en la interfaz mientras el usuario construye una regla.
    """
    variables_dummy = {nombre: MontoDecimal("1.00") for nombre in VARIABLES_DISPONIBLES}
    try:
        evaluar_formula(expresion, variables_dummy)
        return True
    except FormulaError:
        return False
