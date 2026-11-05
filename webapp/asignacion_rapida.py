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
def construir_plantilla_simple(tipo, tiene_iva, afectable_impuestos, cuenta_contraparte,
                                cuenta_banco, empresa, lineas_generales=None):
    """
    Arma la plantilla contable para el caso guiado (asistente
    movimiento por movimiento), con tres capas:

      - `afectable_impuestos=False`: el movimiento NO se toca con IVA
        para nada (ej. nómina, préstamos, movimientos entre cuentas
        propias). Va directo banco <-> cuenta seleccionada, 2 líneas,
        sin importar lo que diga `tiene_iva`.

      - `tiene_iva=True`: movimiento típico con traspaso de IVA
        "por cobrar/pagar" a IVA "causado", 4 líneas balanceadas:

        INGRESO:
          cargo  Bancos                    TOTAL
          cargo  IVA por trasladar         TOTAL/1.16*.16
          abono  IVA trasladado            TOTAL/1.16*.16
          abono  (cuenta que elige el usuario)  TOTAL

        EGRESO:
          abono  Bancos                    TOTAL
          abono  IVA por acreditar         TOTAL/1.16*.16
          cargo  IVA acreditable           TOTAL/1.16*.16
          cargo  (cuenta que elige el usuario)  TOTAL

        Nótese que la cuenta que elige el usuario se afecta por el
        TOTAL (no por la BASE): el IVA se mueve aparte, entre las
        cuentas de IVA por trasladar/acreditar y IVA trasladado/
        acreditable, no contra la cuenta de la contraparte.

      - `lineas_generales`: líneas adicionales que la empresa configuró
        en Configuración > Reglas generales para TODOS sus ingresos o
        egresos (ej. algunas empresas además llevan "Ingresos por
        aplicar / Ingresos"). Se agregan después del bloque básico.
    """
    cuenta_banco = "BANCO"
    cuenta_contraparte = cuenta_contraparte.strip()
    lineas_generales = lineas_generales or []

    if not afectable_impuestos:
        # Caso nómina/no fiscal: solo banco <-> cuenta seleccionada.
        if tipo == "egreso":
            return [
                {"cuenta": cuenta_contraparte, "naturaleza": "cargo", "formula": "TOTAL", "descripcion_linea": None},
                {"cuenta": cuenta_banco, "naturaleza": "abono", "formula": "TOTAL", "descripcion_linea": None},
            ]
        return [
            {"cuenta": cuenta_banco, "naturaleza": "cargo", "formula": "TOTAL", "descripcion_linea": None},
            {"cuenta": cuenta_contraparte, "naturaleza": "abono", "formula": "TOTAL", "descripcion_linea": None},
        ]

    if tipo == "egreso":
        if tiene_iva:
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

def validar_cuentas_iva_configuradas(tipo, tiene_iva, afectable_impuestos, empresa):
    """Regresa un mensaje de error si falta configurar alguna de las
    cuentas de IVA necesarias, para avisar antes de intentar crear la
    regla. Con el movimiento típico de 4 líneas hacen falta AMBAS
    cuentas del par (la "por trasladar/acreditar" y la "trasladado/
    acreditable"), no solo una."""
    if not afectable_impuestos or not tiene_iva:
        return None
    if tipo == "egreso":
        faltantes = [
            nombre for nombre, campo in (
                ("IVA por acreditar", "cuenta_iva_por_acreditar"),
                ("IVA acreditable", "cuenta_iva_acreditable"),
            ) if not empresa[campo]
        ]
        if faltantes:
            return (
                "Este movimiento tiene IVA, pero falta configurar en Configuración la(s) "
                f"cuenta(s): {', '.join(faltantes)}."
            )
    if tipo == "ingreso":
        faltantes = [
            nombre for nombre, campo in (
                ("IVA por trasladar", "cuenta_iva_por_trasladar"),
                ("IVA trasladado", "cuenta_iva_trasladado"),
            ) if not empresa[campo]
        ]
        if faltantes:
            return (
                "Este movimiento tiene IVA, pero falta configurar en Configuración la(s) "
                f"cuenta(s): {', '.join(faltantes)}."
            )
    return None

