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
import re
import pandas as pd

def es_fecha(texto: str) -> bool:
    texto = str(texto).strip()
    if not texto: return False
    # Regex basico para cazar fechas comunes (dd/mm/yyyy, yyyy-mm-dd, etc)
    # Tambien textos como 12 Dic 2023
    patrones = [
        r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}', # 12/12/2023 o 12-12-23
        r'\d{4}[/-]\d{1,2}[/-]\d{1,2}', # 2023-12-12
        r'\d{1,2}\s+(ene|feb|mar|abr|may|jun|jul|ago|sep|oct|nov|dic)[a-z]*\s+\d{2,4}' # 12 dic 2023
    ]
    for p in patrones:
        if re.search(p, texto, re.IGNORECASE):
            return True
    return False

def extraer_numero(texto) -> float:
    try:
        if pd.isna(texto) or texto is None or str(texto).strip() == '': return None
        texto = str(texto).replace('$', '').replace(',', '').strip()
        # Manejar formato contable (1,234.50)
        return float(texto)
    except:
        return None

def detectar_columnas(df: pd.DataFrame, fila_encabezado: int = 0) -> dict:
    """
    Analiza un DataFrame asumiendo que los datos reales empiezan en fila_encabezado+1.
    Retorna sugerencias para el mapeo.
    """
    sugerencias = {
        "formato": "columnas_separadas",
        "columna_fecha": None,
        "columnas_descripcion": [],
        "columna_ingresos": None,
        "columna_egresos": None,
        "columna_importe": None
    }
    
    if len(df) <= fila_encabezado + 1:
        return sugerencias
        
    df_datos = df.iloc[fila_encabezado + 1: fila_encabezado + 21].copy()
    columnas = df.iloc[fila_encabezado].fillna("Vacia").astype(str).tolist()
    
    # Manejar columnas duplicadas
    cols_series = pd.Series(columnas)
    for dup in cols_series[cols_series.duplicated()].unique():
        cols_series[cols_series[cols_series == dup].index.values.tolist()] = [dup + '_' + str(i) if i != 0 else dup for i in range(sum(cols_series == dup))]
    columnas = cols_series.tolist()
    
    df_datos.columns = columnas
    
    scores_fecha = {}
    scores_numerico = {}
    scores_texto = {}
    
    for col in columnas:
        validos_fecha = 0
        validos_num = 0
        negativos = 0
        textos_largos = 0
        total = len(df_datos)
        
        for val in df_datos[col]:
            if pd.isna(val): continue
            str_val = str(val).strip()
            if not str_val: continue
            
            if es_fecha(str_val):
                validos_fecha += 1
                
            num = extraer_numero(str_val)
            if num is not None:
                validos_num += 1
                if num < 0:
                    negativos += 1
                    
            if not es_fecha(str_val) and num is None and len(str_val) > 10:
                textos_largos += 1
                
        scores_fecha[col] = validos_fecha / total if total else 0
        scores_numerico[col] = (validos_num, negativos, validos_num / total if total else 0)
        scores_texto[col] = textos_largos / total if total else 0

    # 1. Detectar Fecha (la columna con mayor score_fecha y > 50%)
    candidatos_fecha = [c for c, s in scores_fecha.items() if s > 0.5]
    if candidatos_fecha:
        # Si hay varios, elegir el primero o el que se llame 'fecha'
        candidatos_fecha.sort(key=lambda c: (1 if 'fech' in c.lower() else 0, scores_fecha[c]), reverse=True)
        sugerencias["columna_fecha"] = candidatos_fecha[0]

    # 2. Detectar Descripcion (texto largo)
    candidatos_desc = [c for c, s in scores_texto.items() if s > 0.4]
    if candidatos_desc:
        candidatos_desc.sort(key=lambda c: (1 if 'concept' in c.lower() or 'descrip' in c.lower() else 0, scores_texto[c]), reverse=True)
        sugerencias["columnas_descripcion"] = [candidatos_desc[0]]
        
    # 3. Detectar Importes
    candidatos_num = [c for c, data in scores_numerico.items() if data[2] > 0.4 and c != sugerencias.get("columna_fecha")]
    candidatos_num.sort(key=lambda c: (1 if any(k in c.lower() for k in ('cargo', 'abono', 'importe', 'retiro', 'deposito', 'monto', 'total')) else 0, scores_numerico[c][2]), reverse=True)
    
    if len(candidatos_num) >= 2:
        sugerencias["formato"] = "columnas_separadas"
        # Diferenciar cargo y abono
        col1 = candidatos_num[0]
        col2 = candidatos_num[1]
        # Heuristica de nombre
        if 'cargo' in col1.lower() or 'retiro' in col1.lower() or 'egreso' in col1.lower():
            sugerencias["columna_egresos"] = col1
            sugerencias["columna_ingresos"] = col2
        elif 'cargo' in col2.lower() or 'retiro' in col2.lower() or 'egreso' in col2.lower():
            sugerencias["columna_egresos"] = col2
            sugerencias["columna_ingresos"] = col1
        else:
            sugerencias["columna_egresos"] = col1
            sugerencias["columna_ingresos"] = col2
    elif len(candidatos_num) == 1:
        col = candidatos_num[0]
        if scores_numerico[col][1] > 0:
            sugerencias["formato"] = "columna_con_signo"
            sugerencias["columna_importe"] = col
        else:
            sugerencias["formato"] = "valor_absoluto_y_tipo"
            sugerencias["columna_importe"] = col

    # 4. Detectar Columna de Tipo de Movimiento (para valor_absoluto_y_tipo)
    candidatos_tipo = []
    for col in df_datos.columns:
        c_low = col.lower()
        if col == sugerencias.get("columna_fecha") or col == sugerencias.get("columna_importe"):
            continue
        vals = [str(v).strip().upper() for v in df_datos[col].dropna()[:30]]
        tiene_tipos = any(v in ("INGRESO", "EGRESO", "CARGO", "ABONO", "DEPOSITO", "RETIRO", "I", "E", "C", "A") for v in vals)
        puntuacion = 0
        if tiene_tipos:
            puntuacion += 10
        if any(k in c_low for k in ("tipo", "naturaleza", "concepto", "movimiento")):
            puntuacion += 5
        if puntuacion > 0:
            candidatos_tipo.append((col, puntuacion))
    if candidatos_tipo:
        candidatos_tipo.sort(key=lambda x: x[1], reverse=True)
        sugerencias["columna_tipo"] = candidatos_tipo[0][0]
        if sugerencias.get("formato") != "columnas_separadas":
            sugerencias["formato"] = "valor_absoluto_y_tipo"

    # 5. Detectar Columna de Factura(s)
    candidatos_fact = []
    for col in df_datos.columns:
        c_low = col.lower()
        if col in (sugerencias.get("columna_fecha"), sugerencias.get("columna_importe"), sugerencias.get("columna_tipo")):
            continue
        if any(k in c_low for k in ("factura", "folio", "num de factura", "no. factura", "fac", "invoices")):
            candidatos_fact.append(col)
    if candidatos_fact:
        sugerencias["columna_factura"] = candidatos_fact[0]

    # 6. Detectar Columna de RFC
    candidatos_rfc = []
    for col in df_datos.columns:
        c_low = col.lower()
        if col in (sugerencias.get("columna_fecha"), sugerencias.get("columna_importe"), sugerencias.get("columna_tipo"), sugerencias.get("columna_factura")):
            continue
        # Revisar si se llama RFC o si sus valores parecen RFCs
        vals = [str(v).strip().upper() for v in df_datos[col].dropna()[:30]]
        import re
        coincidencias_rfc = sum(1 for v in vals if re.search(r'[A-Z&Ñ]{3,4}\d{6}[A-V1-9][A-Z1-9][0-9A]', v))
        puntuacion = (coincidencias_rfc / len(vals)) if vals else 0
        if any(k in c_low for k in ("rfc", "tax_id", "denominacion", "contraparte", "proveedor", "cliente")):
            puntuacion += 1.0
        if puntuacion > 0.3:
            candidatos_rfc.append((col, puntuacion))
    if candidatos_rfc:
        candidatos_rfc.sort(key=lambda x: x[1], reverse=True)
        sugerencias["columna_rfc"] = candidatos_rfc[0][0]

    # 7. Detectar Columna de Referencia Bancaria / Cheque / Rastreo
    candidatos_ref = []
    for col in df_datos.columns:
        c_low = col.lower()
        if col in (sugerencias.get("columna_fecha"), sugerencias.get("columna_importe"), sugerencias.get("columna_tipo"), sugerencias.get("columna_factura"), sugerencias.get("columna_rfc")):
            continue
        if any(k in c_low for k in ("referencia", "transaccion", "cheque", "autorizacion", "rastreo", "clave de rastreo")):
            candidatos_ref.append(col)
    if candidatos_ref:
        sugerencias["columna_referencia"] = candidatos_ref[0]

    return sugerencias

def detectar_fila_y_columnas(df: pd.DataFrame, filas_a_revisar: int = 20) -> tuple[int, dict]:
    """
    Intenta buscar la fila de encabezado por saltos de densidad,
    y luego ejecuta detectar_columnas.
    """
    if df.empty: return 0, {}
    
    densidades = []
    for i in range(min(filas_a_revisar, len(df))):
        fila = df.iloc[i]
        no_nulos = sum(1 for val in fila if not pd.isna(val) and str(val).strip())
        densidades.append(no_nulos)
        
    mejor_fila = 0
    # Buscar el mayor salto de densidad
    for i in range(1, len(densidades)):
        if densidades[i] > densidades[i-1] and densidades[i] >= 3:
            # Validar si tiene sentido
            fila_texto = " ".join([str(v).lower() for v in df.iloc[i] if not pd.isna(v)])
            if 'fech' in fila_texto or 'concept' in fila_texto or 'cargo' in fila_texto or 'monto' in fila_texto or 'importe' in fila_texto:
                mejor_fila = i
                break
                
    # Si no encontro nada claro, por defecto 0
    sugerencias = detectar_columnas(df, mejor_fila)
    return mejor_fila, sugerencias

