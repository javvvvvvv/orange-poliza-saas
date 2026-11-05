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
Pruebas del motor de fórmulas: casos normales + intentos de inyección.

Esto es importante porque las fórmulas las va a escribir el usuario final
(un contador) desde la interfaz web. Si usáramos eval() a secas, alguien
podría escribir algo como:

    __import__('os').system('rm -rf /')

en el campo de fórmula de una regla y tumbar el servidor. Por eso
formula_engine.py parsea con `ast` y solo permite un subconjunto ínfimo
de operaciones. Estas pruebas documentan y verifican esa garantía.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "core"))

from formula_engine import evaluar_formula, FormulaError, construir_variables  # noqa: E402

VARIABLES = construir_variables(total=11600, tasa_iva=0.16, tiene_iva=True,
                                 ret_iva=100, ret_isr=200, tipo_cambio=17.5)

CASOS_VALIDOS = [
    ("TOTAL", 11600.0),
    ("TOTAL / 1.16", 10000.0),
    ("TOTAL * 0.16 / 1.16", 1600.0),
    ("BASE", 10000.0),
    ("IVA", 1600.0),
    ("TOTAL - RET_IVA - RET_ISR", 11300.0),
    ("(TOTAL / 1.16) * 0.16", 1600.0),
    ("round(TOTAL / 3, 2)", 3866.67),
    ("TOTAL * TIPO_CAMBIO", 203000.0),
    ("-RET_IVA", -100.0),
]

CASOS_INVALIDOS_INYECCION = [
    "__import__('os').system('echo hackeado')",
    "open('/etc/passwd').read()",
    "[x for x in ().__class__.__base__.__subclasses__()]",
    "TOTAL.__class__",
    "exec('print(1)')",
    "lambda: TOTAL",
    "TOTAL if True else 0",
    "import os",
    "1; 2",
]

CASOS_INVALIDOS_VARIABLES = [
    "TOTAL + VARIABLE_QUE_NO_EXISTE",
    "SALARIO_DEL_DUENIO",
]

CASOS_INVALIDOS_SINTAXIS = [
    "TOTAL / ",
    "((TOTAL",
    "",
    "   ",
]

def correr_pruebas():
    fallos = 0
    total_pruebas = 0

    print("=" * 70)
    print("PRUEBAS: fórmulas válidas")
    print("=" * 70)
    for expresion, esperado in CASOS_VALIDOS:
        total_pruebas += 1
        try:
            resultado = evaluar_formula(expresion, VARIABLES)
            ok = abs(resultado - esperado) < 0.01
            estado = "✓" if ok else "✗"
            if not ok:
                fallos += 1
            print(f"  {estado} '{expresion}' -> {resultado} (esperado {esperado})")
        except FormulaError as e:
            fallos += 1
            print(f"  ✗ '{expresion}' -> ERROR inesperado: {e}")

    print("\n" + "=" * 70)
    print("PRUEBAS: intentos de inyección de código (TODOS deben ser rechazados)")
    print("=" * 70)
    for expresion in CASOS_INVALIDOS_INYECCION:
        total_pruebas += 1
        try:
            resultado = evaluar_formula(expresion, VARIABLES)
            fallos += 1
            print(f"  ✗ PELIGRO: '{expresion}' se ejecutó y regresó {resultado!r} "
                  f"(¡debía ser rechazado!)")
        except FormulaError:
            print(f"  ✓ Rechazado correctamente: '{expresion}'")
        except Exception as e:
            # Cualquier otra excepción también significa que no se ejecutó
            # código arbitrario exitosamente, pero lo marcamos para revisar
            # el tipo de error.
            print(f"  ✓ Rechazado (con {type(e).__name__}): '{expresion}'")

    print("\n" + "=" * 70)
    print("PRUEBAS: variables inexistentes (deben rechazarse con mensaje claro)")
    print("=" * 70)
    for expresion in CASOS_INVALIDOS_VARIABLES:
        total_pruebas += 1
        try:
            evaluar_formula(expresion, VARIABLES)
            fallos += 1
            print(f"  ✗ '{expresion}' no debió evaluarse sin error")
        except FormulaError as e:
            print(f"  ✓ Rechazado: '{expresion}' -> {e}")

    print("\n" + "=" * 70)
    print("PRUEBAS: sintaxis inválida")
    print("=" * 70)
    for expresion in CASOS_INVALIDOS_SINTAXIS:
        total_pruebas += 1
        try:
            evaluar_formula(expresion, VARIABLES)
            fallos += 1
            print(f"  ✗ {expresion!r} no debió evaluarse sin error")
        except FormulaError as e:
            print(f"  ✓ Rechazado: {expresion!r} -> {e}")

    print("\n" + "=" * 70)
    if fallos == 0:
        print(f"TODO CORRECTO: {total_pruebas}/{total_pruebas} pruebas pasaron.")
    else:
        print(f"FALLARON {fallos} de {total_pruebas} pruebas.")
    print("=" * 70)

    return fallos == 0

if __name__ == "__main__":
    exito = correr_pruebas()
    sys.exit(0 if exito else 1)

