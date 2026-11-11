# ============================================================================
# PROPIEDAD INTELECTUAL Y LICENCIA COMERCIAL CERRADA
# ============================================================================
# Autor Legal y Titular de Derechos: JAVIER ILLAN GONZALEZ
# Organización: ORANGE CREW
# Contacto: ILLANJAVIER9@GMAIL.COM
# ============================================================================
import io
import openpyxl
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter


def generar_plantilla_excel_bytes() -> bytes:
    """
    Genera un archivo Excel (.xlsx) en memoria con la estructura ideal
    de estado de cuenta bancario para Orange Póliza, incluyendo ejemplos
    reales de contabilidad y una pestaña de guía rápida.
    """
    wb = openpyxl.Workbook()

    # -------------------------------------------------------------
    # PESTAÑA 1: Movimientos Ejemplo
    # -------------------------------------------------------------
    ws1 = wb.active
    ws1.title = "Movimientos Ejemplo"
    ws1.views.sheetView[0].showGridLines = True

    # Estilos
    fuente_titulo = Font(name="Calibri", size=14, bold=True, color="1E293B")
    fuente_subtitulo = Font(name="Calibri", size=10, italic=True, color="64748B")
    fuente_encabezado = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    fill_encabezado = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid")
    fill_verde = PatternFill(start_color="DCFCE7", end_color="DCFCE7", fill_type="solid")
    fill_rojo = PatternFill(start_color="FEE2E2", end_color="FEE2E2", fill_type="solid")
    fill_zebra = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")

    borde_fino = Side(style="thin", color="CBD5E1")
    border_box = Border(left=borde_fino, right=borde_fino, top=borde_fino, bottom=borde_fino)
    border_encabezado = Border(
        left=Side(style="thin", color="475569"),
        right=Side(style="thin", color="475569"),
        top=Side(style="medium", color="0F172A"),
        bottom=Side(style="medium", color="0F172A"),
    )

    # Membrete informativo
    ws1["A1"] = "PLANTILLA OFICIAL DE ESTADO DE CUENTA — ORANGE PÓLIZA"
    ws1["A1"].font = fuente_titulo
    ws1["A2"] = "Estructura recomendada (Formato de Columnas Separadas: Ingresos y Egresos)"
    ws1["A2"].font = fuente_subtitulo

    # Encabezados en Fila 4
    encabezados = [
        "Fecha",
        "Concepto / Descripción",
        "Depósitos (Ingresos)",
        "Retiros (Egresos)",
        "RFC Contraparte",
        "Referencia / Folio",
        "Saldo",
    ]

    fila_header = 4
    for col_idx, enc in enumerate(encabezados, 1):
        cell = ws1.cell(row=fila_header, column=col_idx, value=enc)
        cell.font = fuente_encabezado
        cell.fill = fill_encabezado
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = border_encabezado
    ws1.row_dimensions[fila_header].height = 28

    # Filas de ejemplo realistas
    ejemplos = [
        ("2026-11-01", "PAGO DE FACTURA CLIENTE FARMACIA GUADALAJARA", 25000.00, None, "FGU830501XX1", "REF001928", 125000.00),
        ("2026-11-02", "PAGO A PROVEEDOR DISTRIBUIDORA MEDICA SA", None, 14500.00, "DME120405AB2", "SPEI992811", 110500.00),
        ("2026-11-03", "COMISION POR TRANSFERENCIA SPEI", None, 5.00, "BNM840515VB1", "COM00421", 110495.00),
        ("2026-11-03", "IVA POR COMISION BANCARIA 16%", None, 0.80, "BNM840515VB1", "IVA00421", 110494.20),
        ("2026-11-04", "DISPERSION DE NOMINA QUINCENAL BANCO", None, 32000.00, None, "NOM20261101", 78494.20),
        ("2026-11-05", "COBRO TERMINAL PUNTO DE VENTA VENTAS MOSTRADOR", 8750.50, None, None, "TPV88310", 87244.70),
        ("2026-11-06", "PAGO DE SERVICIOS CFE SUMINISTRADOR BASICO", None, 3420.00, "CSS160330CP7", "CFE99120", 83824.70),
        ("2026-11-07", "TRASPASO ENTRE CUENTAS PROPIAS BBVA A BANAMEX", 50000.00, None, None, "TRAS9911", 133824.70),
        ("2026-11-08", "COMPRA DE PAPELERIA Y MATERIAL DE OFICINA", None, 850.00, "OFI920101XYZ", "TICK4412", 132974.70),
        ("2026-11-09", "INTERESES GANADOS INVERSION A PLAZO", 315.20, None, None, "INT202611", 133289.90),
    ]

    for r_idx, fila in enumerate(ejemplos, start=fila_header + 1):
        ws1.row_dimensions[r_idx].height = 20
        es_par = (r_idx % 2 == 0)

        for c_idx, val in enumerate(fila, 1):
            cell = ws1.cell(row=r_idx, column=c_idx, value=val)
            cell.font = Font(name="Calibri", size=10, color="0F172A")
            cell.border = border_box

            if es_par:
                cell.fill = fill_zebra

            # Formatos numéricos y alineación
            if c_idx == 1:  # Fecha
                cell.alignment = Alignment(horizontal="center", vertical="center")
            elif c_idx == 2:  # Concepto
                cell.alignment = Alignment(horizontal="left", vertical="center")
            elif c_idx in (3, 4, 7):  # Importes
                cell.number_format = "$#,##0.00"
                cell.alignment = Alignment(horizontal="right", vertical="center")
                if c_idx == 3 and val:
                    cell.fill = fill_verde
                elif c_idx == 4 and val:
                    cell.fill = fill_rojo
            elif c_idx in (5, 6):  # RFC / Referencia
                cell.alignment = Alignment(horizontal="center", vertical="center")

    # Ajustar anchos de columnas
    anchos = [14, 45, 22, 22, 18, 20, 18]
    for i, w in enumerate(anchos, 1):
        ws1.column_dimensions[get_column_letter(i)].width = w

    # -------------------------------------------------------------
    # PESTAÑA 2: Guía de Formatos
    # -------------------------------------------------------------
    ws2 = wb.create_sheet(title="Guía de Formatos")
    ws2.views.sheetView[0].showGridLines = True

    ws2["A1"] = "GUÍA DE FORMATOS ACEPTADOS POR ORANGE PÓLIZA"
    ws2["A1"].font = fuente_titulo

    lineas_guia = [
        ("¿Cómo deben venir los movimientos?", "Orange Póliza es flexible y acepta cualquier formato bancario. Los 3 más comunes son:"),
        ("", ""),
        ("Formato 1: Columnas Separadas (Recomendado)", "Una columna para Fecha, una para Concepto, una columna exclusiva para Ingresos/Depósitos y otra para Egresos/Retiros. Este es el formato de la primera pestaña."),
        ("Formato 2: Una columna con Signo (+ / -)", "Una sola columna de importe donde los depósitos son positivos (+1,500.00) y los retiros son negativos (-850.00)."),
        ("Formato 3: Importe positivo + Columna Tipo", "Una columna con el importe en valor absoluto (siempre positivo) y otra columna de texto que dice 'ABONO'/'CARGO' o 'DEPOSITO'/'RETIRO'."),
        ("", ""),
        ("Columnas opcionales de gran utilidad:", ""),
        ("• RFC Contraparte:", "Si el estado de cuenta incluye el RFC del cliente/proveedor, el sistema lo conciliará de forma 100% automática con tus facturas CFDI."),
        ("• Referencia / Folio:", "Permite rastrear números de autorización SPEI, cheques o folios bancarios."),
        ("• Fila de encabezado:", "No te preocupes si el banco pone logos o membretes arriba; el asistente de Orange Póliza te permite indicar en qué fila inician los encabezados."),
    ]

    r_guia = 3
    for titulo_seccion, desc in lineas_guia:
        if titulo_seccion:
            cell_t = ws2.cell(row=r_guia, column=1, value=titulo_seccion)
            cell_t.font = Font(name="Calibri", size=11, bold=True, color="1E293B")
        if desc:
            cell_d = ws2.cell(row=r_guia, column=2, value=desc)
            cell_d.font = Font(name="Calibri", size=10, color="334155")
        r_guia += 1

    ws2.column_dimensions["A"].width = 38
    ws2.column_dimensions["B"].width = 85

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
