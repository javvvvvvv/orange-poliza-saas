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
PIPELINE COMPLETO DE PUNTA A PUNTA - Orange Poliza Engine

Este es el demo "de verdad": toma un estado de cuenta Excel tal como
llegaría de un banco, con movimientos mezclados de distintos conceptos,
lo procesa con el motor de reglas, genera las pólizas, cuadra cada una,
y exporta el archivo final en formato Contpaqi. Al final imprime el
dashboard de resumen tal como lo vería un usuario en la interfaz web.

Corre con: python3 pipeline_demo.py
"""

import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "core"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "importers"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "exporters"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app", "cfdi"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "webapp"))

from generar_estado_cuenta_ejemplo import generar_estado_cuenta_ejemplo   # noqa: E402
from generar_cfdis_ejemplo import generar_cfdis_ejemplo, RFC_EMPRESA      # noqa: E402
from excel_importer import importar_excel, ConfiguracionImportacion, FORMATO_COLUMNAS_SEPARADAS  # noqa: E402
from rule_engine import encontrar_regla                                    # noqa: E402
from learning_engine import sugerir_regla_desde_clasificacion_manual      # noqa: E402
from policy_generator import generar_poliza                                # noqa: E402
from contpaqi_exporter import MovimientoPoliza, ImpuestoPoliza, exportar_polizas_contpaqi  # noqa: E402
from rules_repository import RepositorioReglas                             # noqa: E402
from cfdi_importer import importar_carpeta_cfdi                            # noqa: E402
from cfdi_matcher import conciliar, aplicar_conciliaciones_a_movimientos   # noqa: E402

CUENTA_BANCO = "1020-001"  # cuenta contable del banco; se usa en las filas I/V (F4) al exportar

def linea(caracter="-", n=70):
    print(caracter * n)

def main():
    print("â•”" + "â•" * 68 + "â•—")
    print("â•‘  ORANGE POLIZA ENGINE — Pipeline completo (Excel -> Pólizas)     â•‘")
    print("â•š" + "â•" * 68 + "â•")

    # -----------------------------------------------------------------
    # PASO 1: generar y luego importar un estado de cuenta de ejemplo
    # -----------------------------------------------------------------
    ruta_excel = os.path.join(os.path.dirname(__file__), "estado_cuenta_ejemplo.xlsx")
    generar_estado_cuenta_ejemplo(ruta_excel)

    print("\n[1/5] Importando estado de cuenta desde Excel...")
    config_importacion = ConfiguracionImportacion(
        formato=FORMATO_COLUMNAS_SEPARADAS,
        columna_fecha="Fecha",
        columna_ingresos="Ingresos",
        columna_egresos="Egresos",
        columnas_descripcion=["Concepto"],
        columna_rfc_contraparte="RFC",
        columna_referencia="Referencia",
        columna_numero_factura="Factura",
    )
    resultado_importacion = importar_excel(ruta_excel, config_importacion)
    movimientos = resultado_importacion["movimientos"]
    print(f"      Filas en el archivo   : {resultado_importacion['total_filas']}")
    print(f"      Movimientos válidos   : {resultado_importacion['filas_importadas']}")
    print(f"      Filas saltadas        : {resultado_importacion['filas_saltadas']}")

    # -----------------------------------------------------------------
    # PASO 1.5: importar los CFDI (XML emitidos y recibidos) y conciliarlos
    #           automáticamente contra los movimientos bancarios. Esto es
    #           lo que pediste: "que el sistema vea si algún XML coincide
    #           con un movimiento de banco, y si es así, lo tome y lo
    #           asocie". Aquí es donde numero_factura deja de estar vacío.
    # -----------------------------------------------------------------
    print("\n[1.5/5] Importando CFDI (XML) y conciliando contra los movimientos...")
    carpeta_cfdi = os.path.join(os.path.dirname(__file__), "cfdi_ejemplo")
    generar_cfdis_ejemplo(carpeta_cfdi)  # en producción, esto es la carpeta donde se suben los XML

    resultado_cfdi = importar_carpeta_cfdi(carpeta_cfdi)
    print(f"      CFDIs leídos           : {len(resultado_cfdi['cfdis'])}")
    if resultado_cfdi["errores"]:
        for err in resultado_cfdi["errores"]:
            print(f"      ✗ {err['archivo']}: {err['error']}")

    resultado_conciliacion = conciliar(
        cfdis=resultado_cfdi["cfdis"], movimientos=movimientos, rfc_empresa=RFC_EMPRESA,
    )
    aplicar_conciliaciones_a_movimientos(resultado_conciliacion["conciliaciones"])

    print(f"      CFDIs conciliados      : {len(resultado_conciliacion['conciliaciones'])}")
    for c in resultado_conciliacion["conciliaciones"]:
        print(f"        ✓ CFDI {c.cfdi.serie}{c.cfdi.folio} (${c.cfdi.total:,.2f}) "
              f"-> movimiento '{c.movimiento['descripcion'][:40]}'  "
              f"(confianza {c.confianza}%)")
        print(f"          {c.motivo}")
    if resultado_conciliacion["cfdis_sin_movimiento"]:
        print(f"      CFDIs sin movimiento   : {len(resultado_conciliacion['cfdis_sin_movimiento'])}")
        for item in resultado_conciliacion["cfdis_sin_movimiento"]:
            print(f"        ✗ CFDI {item['cfdi'].serie}{item['cfdi'].folio}: {item['motivo']}")

    # -----------------------------------------------------------------
    # PASO 2: reglas de la empresa, en un repositorio SQLite PERSISTENTE.
    #         Aquí es donde se ve "agregar y quitar reglas" de verdad:
    #         no es una lista en memoria que se pierde al cerrar el
    #         script, es un archivo .db que sigue ahí la próxima vez.
    # -----------------------------------------------------------------
    print("\n[2/5] Cargando / configurando reglas de la empresa (persistentes en SQLite)...")

    ruta_db_reglas = os.path.join(os.path.dirname(__file__), "reglas_empresa_demo.db")
    primera_vez = not os.path.exists(ruta_db_reglas)
    repo = RepositorioReglas(ruta_db_reglas)
    repo.inicializar()

    EMPRESA_ID = 1

    if primera_vez:
        print("      (primera corrida: sembrando reglas iniciales)")
        repo.crear_regla(
            empresa_id=EMPRESA_ID, nombre="Compras en OXXO", tipo_movimiento="egreso",
            palabras_clave=["OXXO"],
            plantilla=[
                {"cuenta": "6010-001", "naturaleza": "cargo", "formula": "BASE",
                 "descripcion_linea": "Gastos de operación"},
                {"cuenta": "1180-001", "naturaleza": "cargo", "formula": "IVA",
                 "descripcion_linea": "IVA acreditable"},
                {"cuenta": "1020-001", "naturaleza": "abono", "formula": "TOTAL",
                 "descripcion_linea": "Pago"},
            ],
        )
        repo.crear_regla(
            empresa_id=EMPRESA_ID, nombre="Proveedor ABC - Honorarios", prioridad=5,
            tipo_movimiento="egreso", rfc_contraparte="ABC010101XXX",
            plantilla=[
                {"cuenta": "6010-002", "naturaleza": "cargo", "formula": "BASE",
                 "descripcion_linea": "Honorarios"},
                {"cuenta": "1180-001", "naturaleza": "cargo", "formula": "IVA",
                 "descripcion_linea": "IVA acreditable"},
                {"cuenta": "2105-001", "naturaleza": "abono", "formula": "RET_IVA",
                 "descripcion_linea": "Retención IVA"},
                {"cuenta": "2106-001", "naturaleza": "abono", "formula": "RET_ISR",
                 "descripcion_linea": "Retención ISR"},
                {"cuenta": "1020-001", "naturaleza": "abono", "formula": "TOTAL - RET_IVA - RET_ISR",
                 "descripcion_linea": "Pago transferencia"},
            ],
        )
        repo.crear_regla(
            empresa_id=EMPRESA_ID, nombre="Comisiones bancarias", tipo_movimiento="egreso",
            palabras_clave=["COMISION"],
            plantilla=[
                {"cuenta": "6020-001", "naturaleza": "cargo", "formula": "BASE",
                 "descripcion_linea": "Comisiones bancarias"},
                {"cuenta": "1180-001", "naturaleza": "cargo", "formula": "IVA",
                 "descripcion_linea": "IVA acreditable"},
                {"cuenta": "1020-001", "naturaleza": "abono", "formula": "TOTAL",
                 "descripcion_linea": "Cargo en cuenta"},
            ],
        )
        repo.crear_regla(
            empresa_id=EMPRESA_ID, nombre="Cliente Comercializadora del Norte",
            tipo_movimiento="ingreso", palabras_clave=["COMERCIALIZADORA DEL NORTE"],
            plantilla=[
                {"cuenta": "1020-001", "naturaleza": "cargo", "formula": "TOTAL",
                 "descripcion_linea": "Depósito cliente"},
                {"cuenta": "3000-001", "naturaleza": "abono", "formula": "BASE",
                 "descripcion_linea": "Ventas"},
                {"cuenta": "2001-001", "naturaleza": "abono", "formula": "IVA",
                 "descripcion_linea": "IVA trasladado"},
            ],
        )

        # Regla que TODAVÍA no cubre Netflix a propósito, para agregarla
        # ahora mismo con repo.crear_regla() y demostrar que se puede
        # ir ampliando la cobertura sin tocar el motor.
        print("      + Agregando regla nueva para un movimiento que antes no se cubría: Netflix")
        repo.crear_regla(
            empresa_id=EMPRESA_ID, nombre="Suscripción Netflix", tipo_movimiento="egreso",
            palabras_clave=["NETFLIX"],
            plantilla=[
                {"cuenta": "6015-003", "naturaleza": "cargo", "formula": "TOTAL",
                 "descripcion_linea": "Suscripción software"},
                {"cuenta": "1020-001", "naturaleza": "abono", "formula": "TOTAL",
                 "descripcion_linea": "Pago"},
            ],
        )

        # Y creamos una regla de más (a propósito, mal pensada) solo para
        # demostrar que también se puede QUITAR sin dejar rastro.
        id_regla_de_prueba = repo.crear_regla(
            empresa_id=EMPRESA_ID, nombre="Regla de prueba (se va a borrar)",
            palabras_clave=["ESTO_NO_DEBERIA_QUEDAR"],
            plantilla=[{"cuenta": "0000-000", "naturaleza": "cargo", "formula": "TOTAL"}],
        )
        print(f"      + Regla de prueba creada (id={id_regla_de_prueba}) solo para mostrar cómo se elimina...")
        repo.eliminar_regla(id_regla_de_prueba)
        print(f"      - Regla de prueba (id={id_regla_de_prueba}) eliminada por completo.")
    else:
        print("      (usando reglas ya guardadas de una corrida anterior)")

    reglas, plantillas = repo.cargar_para_motor(EMPRESA_ID)
    print(f"      Reglas activas cargadas desde {os.path.basename(ruta_db_reglas)}: {len(reglas)}")
    for r in reglas:
        print(f"        â€¢ [{r.id}] {r.nombre}"
              + (f"  (RFC={r.rfc_contraparte})" if r.rfc_contraparte else "")
              + (f"  (palabras={r.descripcion_contiene})" if r.descripcion_contiene else ""))

    # -----------------------------------------------------------------
    # PASO 3: aplicar el motor de reglas a cada movimiento
    # -----------------------------------------------------------------
    print("\n[3/5] Clasificando movimientos con el motor de reglas...")

    pendientes_revision = []
    polizas_para_exportar = []
    contador_ingreso = 0
    contador_egreso = 0
    cuadradas = 0
    no_cuadradas = 0

    for mov in movimientos:
        # a las reglas que tienen IVA les asumimos tiene_iva=True salvo
        # que la regla que aplique diga lo contrario (aquí, simplificado,
        # todas las reglas con líneas de IVA lo calculan)
        mov["tiene_iva"] = True
        mov.setdefault("ret_iva", 0.0)
        mov.setdefault("ret_isr", 0.0)
        mov.setdefault("tipo_cambio", 1.0)

        # Para el proveedor ABC calculamos retenciones reales (10.667% / 10%)
        if mov.get("rfc_contraparte") == "ABC010101XXX":
            base_sin_iva = round(mov["total"] / 1.16, 2)
            mov["ret_iva"] = round(base_sin_iva * 0.10667, 2)
            mov["ret_isr"] = round(base_sin_iva * 0.10, 2)

        match = encontrar_regla(mov, reglas)

        if match.regla is None:
            sugerencia = sugerir_regla_desde_clasificacion_manual(mov)
            pendientes_revision.append((mov, sugerencia))
            continue

        plantilla = plantillas[match.regla.id]
        resultado = generar_poliza(
            movimiento=mov, plantilla=plantilla,
            nombre_regla=match.regla.nombre, motivo_match=match.motivo, tasa_iva=0.16,
        )

        if resultado.cuadrada:
            cuadradas += 1
            repo.registrar_uso(match.regla.id)
        else:
            no_cuadradas += 1
            continue  # no exportamos pólizas descuadradas

        if mov["tipo"] == "ingreso":
            contador_ingreso += 1
            numero = contador_ingreso
        else:
            contador_egreso += 1
            numero = contador_egreso

        cfdi_impuestos_obj = [
            ImpuestoPoliza(base=i["base"], importe=i["importe"], tasa=i["tasa"])
            for i in mov.get("cfdi_impuestos", [])
        ]

        polizas_para_exportar.append(MovimientoPoliza(
            numero_poliza=numero,
            tipo=mov["tipo"],
            fecha=mov["fecha"],
            descripcion=mov["descripcion"],
            lineas=resultado.lineas,
            tiene_iva=mov.get("tiene_iva", False),
            numero_factura=mov.get("numero_factura", ""),
            cfdi_uuid=mov.get("cfdi_uuid"),
            cfdi_serie=mov.get("cfdi_uuid") and next(
                (c.cfdi.serie for c in resultado_conciliacion["conciliaciones"]
                 if c.cfdi.uuid == mov.get("cfdi_uuid")), None),
            cfdi_folio=mov.get("cfdi_uuid") and next(
                (c.cfdi.folio for c in resultado_conciliacion["conciliaciones"]
                 if c.cfdi.uuid == mov.get("cfdi_uuid")), None),
            cfdi_impuestos=cfdi_impuestos_obj,
            cuenta_banco=CUENTA_BANCO.replace("-", "") if mov.get("cfdi_uuid") else None,
            ret_iva=mov.get("ret_iva", 0.0),
            ret_isr=mov.get("ret_isr", 0.0),
        ))

    print(f"      Clasificados automáticamente : {len(polizas_para_exportar)}")
    print(f"      Requieren revisión manual     : {len(pendientes_revision)}")
    print(f"      Pólizas cuadradas             : {cuadradas}")
    print(f"      Pólizas NO cuadradas          : {no_cuadradas}")

    # -----------------------------------------------------------------
    # PASO 4: mostrar lo que necesita revisión, con la sugerencia de regla
    # -----------------------------------------------------------------
    if pendientes_revision:
        print("\n[4/5] Movimientos que necesitan revisión manual (con sugerencia):")
        linea()
        for mov, sugerencia in pendientes_revision:
            print(f"  â€¢ {mov['descripcion']}  (${mov['total']:,.2f})")
            print(f"    Sugerencia: regla por '{sugerencia.tipo_coincidencia}' "
                  f"= '{sugerencia.valor_coincidencia}'")
            print(f"    {sugerencia.explicacion}")
            print()
    else:
        print("\n[4/5] No hay movimientos pendientes de revisión.")

    # -----------------------------------------------------------------
    # PASO 5: exportar a Excel formato Contpaqi
    # -----------------------------------------------------------------
    print("[5/5] Exportando pólizas a .xls (formato real de Contpaqi, con CFDI/F4)...")
    ruta_salida = "/mnt/user-data/outputs/polizas_agosto_2026.xls"
    os.makedirs(os.path.dirname(ruta_salida), exist_ok=True)
    info_export = exportar_polizas_contpaqi(polizas_para_exportar, ruta_salida)
    print(f"      Archivo generado      : {info_export['archivo']}")
    print(f"      Pólizas totales       : {info_export['polizas']}")
    print(f"      Con CFDI asociado     : {info_export['polizas_con_cfdi']} (llevan AM/I/W2/V/AD)")
    print(f"      Sin CFDI (solo P/M1)  : {info_export['polizas_sin_cfdi']}")
    print(f"      Filas de datos totales: {info_export['filas_totales_datos']}")

    # -----------------------------------------------------------------
    # DASHBOARD FINAL
    # -----------------------------------------------------------------
    print("\n" + "â•”" + "â•" * 68 + "â•—")
    print("â•‘" + " " * 24 + "RESUMEN DE PROCESAMIENTO" + " " * 20 + "â•‘")
    print("â• " + "â•" * 68 + "â•£")
    total_mov = len(movimientos)
    automaticos = len(polizas_para_exportar)
    pct = (automaticos / total_mov * 100) if total_mov else 0
    print(f"â•‘  Movimientos totales        : {total_mov:<35}â•‘")
    print(f"â•‘  Clasificados automáticos   : {automaticos:<35}â•‘")
    print(f"â•‘  Pendientes de revisión     : {len(pendientes_revision):<35}â•‘")
    print(f"â•‘  % automatizado             : {pct:.1f}%{'':<32}â•‘")
    print("â•š" + "â•" * 68 + "â•")

    print("\nUso acumulado por regla (persistente entre corridas):")
    for r in repo.listar_reglas(EMPRESA_ID):
        estado = "activa" if r["activa"] else "INACTIVA"
        print(f"  [{estado:8s}] {r['nombre']:40s} usada {r['veces_aplicada']} veces")

if __name__ == "__main__":
    main()

