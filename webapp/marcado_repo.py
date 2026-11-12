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
import os
import tempfile

from openpyxl import load_workbook
from openpyxl.styles import PatternFill, Font, Alignment

from db import get_connection

AMARILLO = PatternFill(start_color="FFF9C4", end_color="FFF9C4", fill_type="solid")
ENCABEZADO_FILL = PatternFill(start_color="E0F2FE", end_color="E0F2FE", fill_type="solid")
NEGRITA = Font(bold=True)


def _limpiar_cuenta(cuenta):
    return (str(cuenta or "")).replace("-", "").replace(" ", "").strip()


def generar_marcado(documento_id, empresa_id=None):
    with get_connection() as con:
        if empresa_id is not None:
            documento = con.execute(
                """SELECT d.*, b.cuenta_contable as banco_cuenta 
                   FROM documentos_importados d 
                   LEFT JOIN bancos b ON b.id = d.banco_id 
                   WHERE d.id = ? AND d.empresa_id = ?""",
                (documento_id, empresa_id),
            ).fetchone()
        else:
            documento = con.execute(
                """SELECT d.*, b.cuenta_contable as banco_cuenta 
                   FROM documentos_importados d 
                   LEFT JOIN bancos b ON b.id = d.banco_id 
                   WHERE d.id = ?""",
                (documento_id,),
            ).fetchone()

        if documento is None or not documento["ruta_archivo"]:
            raise ValueError("No se encontró el archivo original de este documento.")

        emp_id = documento["empresa_id"]
        banco_cuenta_clean = _limpiar_cuenta(documento["banco_cuenta"])

        movimientos = con.execute(
            """SELECT m.fila_original, m.fecha, m.descripcion, m.total, 
                      p.id as poliza_id, p.tipo, p.numero, p.cuadrada, p.enviada_contpaqi
               FROM movimientos m
               JOIN polizas p ON p.id = m.poliza_id
               WHERE m.documento_id = ? AND m.fila_original IS NOT NULL
               ORDER BY m.fila_original ASC""",
            (documento_id,),
        ).fetchall()

        # Obtener todas las líneas de las pólizas con descripciones del catálogo
        poliza_ids = list({m["poliza_id"] for m in movimientos if m.get("poliza_id")})
        poliza_contrapartidas = {}

        if poliza_ids:
            placeholders = ",".join(["?"] * len(poliza_ids))
            params = [emp_id] + poliza_ids
            lineas = con.execute(
                f"""SELECT pl.poliza_id, pl.cuenta, pl.naturaleza, pl.importe, c.descripcion as cuenta_nombre
                    FROM poliza_lineas pl
                    LEFT JOIN cuentas_catalogo c ON (REPLACE(c.cuenta, '-', '') = REPLACE(pl.cuenta, '-', '') AND c.empresa_id = ?)
                    WHERE pl.poliza_id IN ({placeholders})
                    ORDER BY pl.poliza_id, pl.orden ASC""",
                tuple(params),
            ).fetchall()

            for l in lineas:
                pid = l["poliza_id"]
                cta_clean = _limpiar_cuenta(l["cuenta"])
                # Excluir la cuenta del banco si está configurada
                if banco_cuenta_clean and cta_clean == banco_cuenta_clean:
                    continue
                if pid not in poliza_contrapartidas:
                    poliza_contrapartidas[pid] = []
                poliza_contrapartidas[pid].append(l)

    wb = load_workbook(documento["ruta_archivo"])
    hoja = documento["nombre_hoja"] or wb.sheetnames[0]
    ws = wb[hoja]

    # Detectar dinámicamente la fila del encabezado escaneando las primeras 30 filas
    header_row = 1
    for r in range(1, min(35, ws.max_row + 1)):
        vals = [str(ws.cell(row=r, column=c).value or "").lower() for c in range(1, min(15, ws.max_column + 1))]
        if any("fecha" in v for v in vals) and any(
            ("concepto" in v or "descripcion" in v or "descripción" in v or "deposito" in v or "retiro" in v or "importe" in v)
            for v in vals
        ):
            header_row = r
            break

    ultima_col = ws.max_column
    col_poliza = ultima_col + 1
    col_cuenta = ultima_col + 2
    col_nombre = ultima_col + 3
    col_estado = ultima_col + 4

    # Escribir los encabezados en la fila detectada
    for col_idx, texto in [
        (col_poliza, "Número de Póliza"),
        (col_cuenta, "Cuenta Contable"),
        (col_nombre, "Nombre de Cuenta"),
        (col_estado, "Estado"),
    ]:
        celda_hdr = ws.cell(row=header_row, column=col_idx, value=texto)
        celda_hdr.font = NEGRITA
        celda_hdr.fill = ENCABEZADO_FILL
        celda_hdr.alignment = Alignment(horizontal="center")

    marcados = 0
    for m in movimientos:
        # La fila real de Excel se calcula a partir del header_row detectado + 1 + fila_original
        fila_excel = header_row + 1 + m["fila_original"]
        if fila_excel > ws.max_row:
            continue

        # Resaltar la fila del movimiento
        for col in range(1, ultima_col + 1):
            ws.cell(row=fila_excel, column=col).fill = AMARILLO

        etiqueta_poliza = f"{'EG' if m['tipo'] == 'egreso' else 'IN'}-{m['numero']}"
        pid = m["poliza_id"]
        contras = poliza_contrapartidas.get(pid, [])

        if contras:
            # Ordenar contrapartidas por importe descendente para priorizar la cuenta principal
            contras_ordenadas = sorted(contras, key=lambda x: float(x["importe"] or 0), reverse=True)
            cuentas_texto = ", ".join(dict.fromkeys(c["cuenta"] for c in contras_ordenadas if c.get("cuenta")))
            nombres_texto = ", ".join(dict.fromkeys(c["cuenta_nombre"] for c in contras_ordenadas if c.get("cuenta_nombre")))
        else:
            cuentas_texto = ""
            nombres_texto = ""

        estado_texto = "Exportada" if m["enviada_contpaqi"] == 1 else ("Cuadrada" if m["cuadrada"] == 1 else "Descuadrada")

        for col_idx, val in [
            (col_poliza, etiqueta_poliza),
            (col_cuenta, cuentas_texto),
            (col_nombre, nombres_texto),
            (col_estado, estado_texto),
        ]:
            celda = ws.cell(row=fila_excel, column=col_idx, value=val)
            celda.font = NEGRITA if col_idx == col_poliza else None
            celda.fill = AMARILLO

        marcados += 1

    # Auto-ajustar ancho de las columnas agregadas
    for col_idx in [col_poliza, col_cuenta, col_nombre, col_estado]:
        col_letter = ws.cell(row=header_row, column=col_idx).column_letter
        ws.column_dimensions[col_letter].width = 22

    ruta_salida = os.path.join(
        tempfile.gettempdir(), f"marcado_{os.path.basename(documento['nombre_archivo'])}"
    )
    wb.save(ruta_salida)
    return ruta_salida, marcados
