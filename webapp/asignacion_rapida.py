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

def es_cuenta_de_resultados(cuenta: str, codigo_agrupador: str = None) -> bool:
    """
    Determina si una cuenta contable es de Resultados (Ingresos 4xx, Costos 5xx, Gastos 6xx, 7xx, 8xx)
    o de Balance (Activo 1xx, Pasivo 2xx, Capital 3xx).
    """
    if codigo_agrupador:
        sat = str(codigo_agrupador).strip()
        if sat and sat[0] in ("4", "5", "6", "7", "8"):
            return True
        if sat and sat[0] in ("1", "2", "3"):
            return False

    clean = (cuenta or "").strip().replace("-", "").replace(".", "").replace(" ", "")
    if clean:
        if clean[0] in ("4", "5", "6", "7", "8"):
            return True
        if clean[0] in ("1", "2", "3"):
            return False
    return False

def construir_plantilla_simple(tipo, tiene_iva, afectable_impuestos, cuenta_contraparte,
                                cuenta_banco, empresa, lineas_generales=None,
                                codigo_agrupador=None):
    """
    Arma la plantilla contable diferenciando automáticamente según la naturaleza de la cuenta:

    1. CUENTAS DE RESULTADOS (Gasto 6xx, Costo 5xx, Ingreso 4xx):
       - Egreso directo (gasto de contado):
           cargo  (cuenta gasto/costo)    BASE
           cargo  IVA acreditable         IVA
           abono  Retención IVA           RET_IVA (si activa y configurada)
           abono  Retención ISR           RET_ISR (si activa y configurada)
           abono  Bancos                  TOTAL
       - Ingreso directo:
           cargo  Bancos                  TOTAL
           cargo  Retención IVA           RET_IVA (si activa y configurada)
           cargo  Retención ISR           RET_ISR (si activa y configurada)
           abono  (cuenta ingreso)        BASE
           abono  IVA trasladado          IVA

    2. CUENTAS DE BALANCE (Proveedores 2xx, Clientes 1xx):
       - Pago a proveedor / flujo de efectivo (4 líneas con reclasificación de IVA):
           abono  Bancos                  TOTAL
           abono  IVA por acreditar       IVA
           cargo  IVA acreditable         IVA
           cargo  (cuenta proveedor)      TOTAL
       - Cobro a cliente:
           cargo  Bancos                  TOTAL
           cargo  IVA por trasladar       IVA
           abono  IVA trasladado          IVA
           abono  (cuenta cliente)        TOTAL

    3. MOVIMIENTOS NO FISCALES O SIN IVA:
       - 2 líneas directas Banco <-> Contraparte por TOTAL.
    """
    cuenta_banco = "BANCO"
    cuenta_contraparte = (cuenta_contraparte or "").strip()
    lineas_generales = lineas_generales or []

    if not afectable_impuestos:
        if tipo == "egreso":
            return [
                {"cuenta": cuenta_contraparte, "naturaleza": "cargo", "formula": "TOTAL", "descripcion_linea": None},
                {"cuenta": cuenta_banco, "naturaleza": "abono", "formula": "TOTAL", "descripcion_linea": None},
            ]
        return [
            {"cuenta": cuenta_banco, "naturaleza": "cargo", "formula": "TOTAL", "descripcion_linea": None},
            {"cuenta": cuenta_contraparte, "naturaleza": "abono", "formula": "TOTAL", "descripcion_linea": None},
        ]

    es_resultados = es_cuenta_de_resultados(cuenta_contraparte, codigo_agrupador)

    if tipo == "egreso":
        if tiene_iva:
            if es_resultados:
                # Gasto directo de contado: BASE a gasto, IVA a acreditable, TOTAL a banco
                base = [
                    {"cuenta": cuenta_contraparte, "naturaleza": "cargo", "formula": "BASE", "descripcion_linea": None},
                    {"cuenta": empresa["cuenta_iva_acreditable"], "naturaleza": "cargo", "formula": "IVA",
                     "descripcion_linea": "IVA acreditable"},
                ]
                if empresa.get("retenciones_activas"):
                    if empresa.get("cuenta_retencion_iva"):
                        base.append({
                            "cuenta": empresa["cuenta_retencion_iva"], "naturaleza": "abono", "formula": "RET_IVA",
                            "descripcion_linea": "Retención IVA",
                        })
                    if empresa.get("cuenta_retencion_isr"):
                        base.append({
                            "cuenta": empresa["cuenta_retencion_isr"], "naturaleza": "abono", "formula": "RET_ISR",
                            "descripcion_linea": "Retención ISR",
                        })
                base.append({"cuenta": cuenta_banco, "naturaleza": "abono", "formula": "TOTAL", "descripcion_linea": None})
            else:
                # Pago a proveedor / pasivo: TOTAL a proveedor con traspaso de IVA
                cuenta_iva_por_acreditar = empresa["cuenta_iva_por_acreditar"]
                cuenta_iva_acreditable = empresa["cuenta_iva_acreditable"]
                base = [
                    {"cuenta": cuenta_banco, "naturaleza": "abono", "formula": "TOTAL", "descripcion_linea": None},
                    {"cuenta": cuenta_iva_por_acreditar, "naturaleza": "abono", "formula": "IVA",
                     "descripcion_linea": "IVA por acreditar"},
                    {"cuenta": cuenta_iva_acreditable, "naturaleza": "cargo", "formula": "IVA",
                     "descripcion_linea": "IVA acreditable"},
                    {"cuenta": cuenta_contraparte, "naturaleza": "cargo", "formula": "TOTAL", "descripcion_linea": None},
                ]
        else:
            base = [
                {"cuenta": cuenta_contraparte, "naturaleza": "cargo", "formula": "TOTAL", "descripcion_linea": None},
                {"cuenta": cuenta_banco, "naturaleza": "abono", "formula": "TOTAL", "descripcion_linea": None},
            ]
    else:  # ingreso
        if tiene_iva:
            if es_resultados:
                # Ingreso directo: TOTAL a banco, BASE a ingreso, IVA a trasladado
                base = [
                    {"cuenta": cuenta_banco, "naturaleza": "cargo", "formula": "TOTAL", "descripcion_linea": None},
                ]
                if empresa.get("retenciones_activas"):
                    if empresa.get("cuenta_retencion_iva"):
                        base.append({
                            "cuenta": empresa["cuenta_retencion_iva"], "naturaleza": "cargo", "formula": "RET_IVA",
                            "descripcion_linea": "Retención IVA",
                        })
                    if empresa.get("cuenta_retencion_isr"):
                        base.append({
                            "cuenta": empresa["cuenta_retencion_isr"], "naturaleza": "cargo", "formula": "RET_ISR",
                            "descripcion_linea": "Retención ISR",
                        })
                base.extend([
                    {"cuenta": cuenta_contraparte, "naturaleza": "abono", "formula": "BASE", "descripcion_linea": None},
                    {"cuenta": empresa["cuenta_iva_trasladado"], "naturaleza": "abono", "formula": "IVA",
                     "descripcion_linea": "IVA trasladado"},
                ])
            else:
                # Cobro a cliente: TOTAL a cliente con traspaso de IVA
                cuenta_iva_por_trasladar = empresa["cuenta_iva_por_trasladar"]
                cuenta_iva_trasladado = empresa["cuenta_iva_trasladado"]
                base = [
                    {"cuenta": cuenta_banco, "naturaleza": "cargo", "formula": "TOTAL", "descripcion_linea": None},
                    {"cuenta": cuenta_iva_por_trasladar, "naturaleza": "cargo", "formula": "IVA",
                     "descripcion_linea": "IVA por trasladar"},
                    {"cuenta": cuenta_iva_trasladado, "naturaleza": "abono", "formula": "IVA",
                     "descripcion_linea": "IVA trasladado"},
                    {"cuenta": cuenta_contraparte, "naturaleza": "abono", "formula": "TOTAL", "descripcion_linea": None},
                ]
        else:
            base = [
                {"cuenta": cuenta_banco, "naturaleza": "cargo", "formula": "TOTAL", "descripcion_linea": None},
                {"cuenta": cuenta_contraparte, "naturaleza": "abono", "formula": "TOTAL", "descripcion_linea": None},
            ]

    extra = [
        {"cuenta": lg["cuenta"], "naturaleza": lg["naturaleza"], "formula": lg["formula"],
         "descripcion_linea": lg["descripcion_linea"]}
        for lg in lineas_generales
    ]
    return base + extra

def validar_cuentas_iva_configuradas(tipo, tiene_iva, afectable_impuestos, empresa,
                                     cuenta_contraparte=None, codigo_agrupador=None):
    """Regresa un mensaje de error si falta configurar alguna de las
    cuentas de IVA necesarias según la naturaleza de la operación."""
    if not afectable_impuestos or not tiene_iva:
        return None

    es_resultados = es_cuenta_de_resultados(cuenta_contraparte, codigo_agrupador) if cuenta_contraparte else False

    if tipo == "egreso":
        if es_resultados:
            faltantes = [nombre for nombre, campo in (("IVA acreditable", "cuenta_iva_acreditable"),) if not empresa.get(campo)]
        else:
            faltantes = [
                nombre for nombre, campo in (
                    ("IVA por acreditar", "cuenta_iva_por_acreditar"),
                    ("IVA acreditable", "cuenta_iva_acreditable"),
                ) if not empresa.get(campo)
            ]
        if faltantes:
            return (
                "Este movimiento tiene IVA, pero falta configurar en Configuración la(s) "
                f"cuenta(s): {', '.join(faltantes)}."
            )

    if tipo == "ingreso":
        if es_resultados:
            faltantes = [nombre for nombre, campo in (("IVA trasladado", "cuenta_iva_trasladado"),) if not empresa.get(campo)]
        else:
            faltantes = [
                nombre for nombre, campo in (
                    ("IVA por trasladar", "cuenta_iva_por_trasladar"),
                    ("IVA trasladado", "cuenta_iva_trasladado"),
                ) if not empresa.get(campo)
            ]
        if faltantes:
            return (
                "Este movimiento tiene IVA, pero falta configurar en Configuración la(s) "
                f"cuenta(s): {', '.join(faltantes)}."
            )

    return None
