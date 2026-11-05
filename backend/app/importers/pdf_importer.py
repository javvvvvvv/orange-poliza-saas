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

import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "core")))
from column_detector import detectar_fila_y_columnas
"""
Importador de estados de cuenta desde PDF.
Usa pdfplumber para extraer las tablas de los PDFs bancarios
y los convierte en un DataFrame de pandas para reusar la logica
de excel_importer.py.
"""

try:
    import pdfplumber
except ImportError:
    pdfplumber = None

import pandas as pd
from typing import Optional
try:
    from backend.app.importers.excel_importer import (
        procesar_dataframe, 
        ConfiguracionImportacion
    )
except ImportError:
    from excel_importer import (
        procesar_dataframe, 
        ConfiguracionImportacion
    )

def _pdf_a_dataframe(ruta_archivo: str) -> pd.DataFrame:
    """
    Extrae todas las tablas de todas las paginas del PDF y las concatena
    en un solo DataFrame. Si hay encabezados repetidos, se deberian limpiar
    automaticamente porque el usuario configurara 'fila_encabezado'.
    """
    todas_las_tablas = []
    
    with pdfplumber.open(ruta_archivo) as pdf:
        for page in pdf.pages:
            tablas = page.extract_tables()
            for t in tablas:
                todas_las_tablas.extend(t)
                
    if not todas_las_tablas:
        return pd.DataFrame()
        
    return pd.DataFrame(todas_las_tablas)

def listar_hojas(ruta_archivo: str) -> list[str]:
    # Un PDF no tiene hojas Excel, devolvemos un nombre generico.
    return ["Tabla extraida del PDF"]

def detectar_fila_encabezado(ruta_archivo: str, nombre_hoja: str = None, filas_a_revisar: int = 20) -> int:
    df = _pdf_a_dataframe(ruta_archivo)
    if df.empty:
        return 0
    
    # Heuristica basica similar a excel: buscar la fila con "fecha", "concepto", "cargo", "abono"
    for idx, fila in df.head(filas_a_revisar).iterrows():
        fila_str = " ".join([str(v).lower() for v in fila.values if v])
        if "fecha" in fila_str and ("cargo" in fila_str or "abono" in fila_str or "importe" in fila_str):
            return idx
    return 0

def vista_previa_cruda(ruta_archivo: str, nombre_hoja: str = None, filas: int = 20) -> dict:
    df = _pdf_a_dataframe(ruta_archivo)
    if df.empty:
        return {"filas": []}
        
    df_preview = df.head(filas).fillna("")
    return {"filas": df_preview.values.tolist()}

def vista_previa(ruta_archivo: str, nombre_hoja: str = None, fila_encabezado: int = 0,
                 filas: int = 15) -> dict:
    df = _pdf_a_dataframe(ruta_archivo)
    if df.empty or len(df) <= fila_encabezado:
        return {"columnas": [], "filas": []}
        
    # Asignar encabezados
    df.columns = df.iloc[fila_encabezado].fillna(f"Col_vacia").astype(str)
    # Cortar los datos
    df = df.iloc[fila_encabezado + 1 :].reset_index(drop=True)
    
    # Manejar columnas duplicadas
    cols = pd.Series(df.columns)
    for dup in cols[cols.duplicated()].unique():
        cols[cols[cols == dup].index.values.tolist()] = [dup + '_' + str(i) if i != 0 else dup for i in range(sum(cols == dup))]
    df.columns = cols
    
    df_preview = df.head(filas).fillna("")
    return {
        "columnas": df.columns.tolist(),
        "filas": df_preview.to_dict('records')
    }

def importar_pdf(ruta_archivo: str, config: ConfiguracionImportacion,
                 nombre_hoja: Optional[str] = None) -> dict:
    df = _pdf_a_dataframe(ruta_archivo)
    if df.empty:
        return {"movimientos": [], "total_filas": 0, "filas_importadas": 0, "filas_saltadas": 0}
        
    # Ajustar el dataframe segun la configuracion
    if config.fila_encabezado < len(df):
        df.columns = df.iloc[config.fila_encabezado].fillna("Col_vacia").astype(str)
        df = df.iloc[config.fila_encabezado + 1 :].reset_index(drop=True)
        
        cols = pd.Series(df.columns)
        for dup in cols[cols.duplicated()].unique():
            cols[cols[cols == dup].index.values.tolist()] = [dup + '_' + str(i) if i != 0 else dup for i in range(sum(cols == dup))]
        df.columns = cols
        
    return procesar_dataframe(df, config)

