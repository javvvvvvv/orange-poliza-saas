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
Demo end-to-end de Orange Poliza Engine (fase "motor").

Este script prueba, con datos de ejemplo y una base SQLite desechable,
el flujo completo que describimos:

  1. Una empresa tiene un catálogo de cuentas y dos reglas configuradas
     (una por palabra clave "OXXO", otra por RFC de un proveedor).
  2. Llegan movimientos nuevos de un estado de cuenta.
  3. El motor de reglas encuentra qué regla aplica (o dice que no
     encontró nada, para clasificación manual).
  4. El generador de pólizas calcula los importes con las fórmulas de
     cada regla (TOTAL/1.16, IVA, etc.) y valida que cuadre.
  5. Se imprime la explicación paso a paso, tal como la vería el usuario
     en el "visor de explicación" de la póliza.

No depende de FastAPI/SQLAlchemy: usa sqlite3 (stdlib) para poder correr
en cualquier lado sin instalar nada.
"""

import os
import sqlite3
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "core"))

from rule_engine import Regla, encontrar_regla, normalizar_texto          # noqa: E402
from policy_generator import LineaPlantilla, generar_poliza                # noqa: E402

def main():
    print("=" * 70)
    print("ORANGE POLIZA ENGINE - Demo del motor de reglas y pólizas")
    print("=" * 70)

    # -----------------------------------------------------------------
    # 1. Reglas configuradas por el usuario para "Empresa Demo SA de CV"
    # -----------------------------------------------------------------

    regla_oxxo = Regla(
        id=1,
        empresa_id=1,
        nombre="Compras en OXXO",
        prioridad=10,
        activa=True,
        descripcion_contiene=["OXXO"],
        tipo_movimiento="egreso",
    )

    regla_proveedor_abc = Regla(
        id=2,
        empresa_id=1,
        nombre="Proveedor ABC - Servicios profesionales",
        prioridad=5,
        activa=True,
        rfc_contraparte="ABC010101XXX",
        tipo_movimiento="egreso",
    )

    reglas = [regla_oxxo, regla_proveedor_abc]

    # Plantillas de cada regla (lo que la regla genera en la póliza)
    plantillas = {
        regla_oxxo.id: [
            LineaPlantilla(cuenta="6010-001", naturaleza="cargo", formula="BASE",
                            descripcion_linea="Gastos de operación - OXXO"),
            LineaPlantilla(cuenta="1180-001", naturaleza="cargo", formula="IVA",
                            descripcion_linea="IVA acreditable - OXXO"),
            LineaPlantilla(cuenta="1020-001", naturaleza="abono", formula="TOTAL",
                            descripcion_linea="Pago OXXO"),
        ],
        regla_proveedor_abc.id: [
            LineaPlantilla(cuenta="6010-002", naturaleza="cargo", formula="BASE",
                            descripcion_linea="Honorarios - Proveedor ABC"),
            LineaPlantilla(cuenta="1180-001", naturaleza="cargo", formula="IVA",
                            descripcion_linea="IVA acreditable - Proveedor ABC"),
            LineaPlantilla(cuenta="2105-001", naturaleza="abono", formula="RET_IVA",
                            descripcion_linea="Retención IVA - Proveedor ABC"),
            LineaPlantilla(cuenta="2106-001", naturaleza="abono", formula="RET_ISR",
                            descripcion_linea="Retención ISR - Proveedor ABC"),
            LineaPlantilla(cuenta="1020-001", naturaleza="abono",
                            formula="TOTAL - RET_IVA - RET_ISR",
                            descripcion_linea="Pago transferencia - Proveedor ABC"),
        ],
    }

    # -----------------------------------------------------------------
    # 2. Movimientos nuevos que llegaron del estado de cuenta
    # -----------------------------------------------------------------

    movimientos = [
        {
            "descripcion": "COMPRA OXXO 4821 SUC 332",
            "tipo": "egreso",
            "total": 348.00,
            "tiene_iva": True,
            "rfc_contraparte": None,
            "cuenta_bancaria_contraparte": None,
        },
        {
            "descripcion": "TRANSFERENCIA SPEI PROVEEDOR ABC HONORARIOS AGOSTO",
            "tipo": "egreso",
            "total": 20000.00,
            "tiene_iva": True,
            "ret_iva": round(20000 / 1.16 * (10.667 / 100), 2),
            "ret_isr": round(20000 / 1.16 * (10 / 100), 2),
            "rfc_contraparte": "ABC010101XXX",
            "cuenta_bancaria_contraparte": None,
        },
        {
            "descripcion": "PAGO SERVICIO DESCONOCIDO XYZ 991",
            "tipo": "egreso",
            "total": 1500.00,
            "tiene_iva": True,
            "rfc_contraparte": None,
            "cuenta_bancaria_contraparte": None,
        },
    ]

    # -----------------------------------------------------------------
    # 3 y 4. Procesar cada movimiento: encontrar regla + generar póliza
    # -----------------------------------------------------------------

    resumen = {"automaticos": 0, "sin_regla": 0, "cuadradas": 0, "no_cuadradas": 0}

    for i, mov in enumerate(movimientos, start=1):
        print(f"\n--- Movimiento #{i} ---")
        print(f"Descripción original : {mov['descripcion']}")
        print(f"Normalizada          : {normalizar_texto(mov['descripcion'])}")
        print(f"Total                : ${mov['total']:,.2f}")

        match = encontrar_regla(mov, reglas)
        print(f"\nResultado del motor de reglas:")
        print(f"  Nivel de coincidencia : {match.nivel}")
        print(f"  Confianza             : {match.confianza}%")
        print(f"  Motivo                : {match.motivo}")

        if match.regla is None:
            resumen["sin_regla"] += 1
            print("\n  -> No se generó póliza. Este movimiento se manda a "
                  "clasificación manual (el usuario crea la regla y el "
                  "sistema la aprende para la próxima vez).")
            continue

        resumen["automaticos"] += 1
        plantilla = plantillas[match.regla.id]

        resultado = generar_poliza(
            movimiento=mov,
            plantilla=plantilla,
            nombre_regla=match.regla.nombre,
            motivo_match=match.motivo,
            tasa_iva=0.16,
        )

        print("\nExplicación de la póliza generada:")
        for linea_exp in resultado.explicacion:
            print(f"  {linea_exp}")

        if resultado.errores:
            print("\n  Errores encontrados:")
            for err in resultado.errores:
                print(f"    ✗ {err}")

        if resultado.cuadrada:
            resumen["cuadradas"] += 1
        else:
            resumen["no_cuadradas"] += 1

    # -----------------------------------------------------------------
    # 5. Resumen final, como el dashboard que describimos
    # -----------------------------------------------------------------

    print("\n" + "=" * 70)
    print("RESUMEN DE AUTOMATIZACIÓN")
    print("=" * 70)
    print(f"  Movimientos procesados      : {len(movimientos)}")
    print(f"  Clasificados automáticamente: {resumen['automaticos']}")
    print(f"  Requieren revisión manual   : {resumen['sin_regla']}")
    print(f"  Pólizas cuadradas           : {resumen['cuadradas']}")
    print(f"  Pólizas NO cuadradas        : {resumen['no_cuadradas']}")

    # -----------------------------------------------------------------
    # Bonus: probar que el esquema SQL de verdad corre en SQLite
    # -----------------------------------------------------------------
    print("\n" + "=" * 70)
    print("Verificando que database/schema.sql es válido...")
    print("=" * 70)
    schema_path = os.path.join(os.path.dirname(__file__), "..", "..", "database", "schema.sql")
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys = OFF")  # sqlite exige orden de creación estricto; lo relajamos para el demo
    with open(schema_path, encoding="utf-8") as f:
        sql = f.read()
    try:
        con.executescript(sql)
        print("✓ El esquema se creó sin errores en una base SQLite en memoria.")
        cur = con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")
        tablas = [r[0] for r in cur.fetchall()]
        print(f"✓ Tablas creadas ({len(tablas)}): {', '.join(tablas)}")
    except sqlite3.Error as e:
        print(f"✗ Error al crear el esquema: {e}")
    finally:
        con.close()

if __name__ == "__main__":
    main()

