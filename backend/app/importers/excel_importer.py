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
# -*- coding: utf-8 -*-
"""
Importador de estados de cuenta desde Excel.

Generaliza lo que ya hacía tuy.py (SistemaContable.procesar_movimientos),
pero como función pura, configurable por parámetros en vez de por
ventanas de Tkinter, para que después la use tanto la API web como
cualquier otro importador (PDF, CSV, XML) sin duplicar lógica.

Soporta los mismos 3 formatos de estado de cuenta que el sistema viejo,
porque en la práctica cada banco exporta distinto:

  FORMATO_COLUMNAS_SEPARADAS
      Una columna de ingresos y otra de egresos (celdas vacías o 0
      cuando no aplica). Ej.: Banorte, algunos reportes de BBVA.

  FORMATO_COLUMNA_CON_SIGNO
      Una sola columna de movimiento, positivo = ingreso, negativo = egreso.

  FORMATO_VALOR_ABSOLUTO_Y_TIPO
      Una columna de importe siempre positivo + una columna de texto
      que dice si es cargo/abono, ingreso/egreso, +/-, etc. (configurable
      por empresa, igual que `simbolos_tipo` en el sistema viejo).

El resultado siempre es una lista de dicts en el "movimiento normalizado"
común que ya consume rule_engine.py y policy_generator.py.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

import pandas as pd

_PATRON_CUENTA_TRAS_SLASH = re.compile(r"/\s*([A-Z0-9]{6,})", re.IGNORECASE)

_PATRON_RFC = re.compile(r'\b[A-Z&]{3,4}[0-9]{6}[A-Z0-9]{3}\b', re.IGNORECASE)

def _detectar_rfc_en_descripcion(descripcion: str):
    m = _PATRON_RFC.search(descripcion or "")
    if m:
        return m.group(0).upper()
    return None

def _detectar_cuenta_en_descripcion(descripcion: str) -> Optional[str]:
    """Si el banco no trae una columna dedicada de 'cuenta contraparte'
    (muy común: todo viene junto en una sola columna de concepto), se
    intenta sacar un número de cuenta/referencia largo después de un
    '/' en el texto — típico de SPEI y pagos a terceros
    ('SPEI ENVIADO BANORTE/0028920544', 'PAGO CUENTA DE TERCERO/
    0031797026'). Sin esto, esos movimientos solo se pueden reconocer
    por palabras sueltas de la descripción ('ENVIADO', 'CUENTA'), que
    son genéricas y terminan agrupando por error movimientos que en
    realidad van a destinos distintos. Ver misma lógica en
    learning_engine._detectar_cuenta_en_texto (duplicada a propósito
    para no acoplar este importador al paquete core)."""
    m = _PATRON_CUENTA_TRAS_SLASH.search(descripcion or "")
    if not m:
        return None
    candidato = m.group(1)
    return candidato if sum(c.isdigit() for c in candidato) >= 6 else None

FORMATO_COLUMNAS_SEPARADAS = "columnas_separadas"
FORMATO_COLUMNA_CON_SIGNO = "columna_con_signo"
FORMATO_VALOR_ABSOLUTO_Y_TIPO = "valor_absoluto_y_tipo"

FORMATOS_FECHA_SOPORTADOS = [
    "%d/%m/%Y", "%m/%d/%Y", "%Y/%m/%d", "%Y/%d/%m",
    "%d-%m-%Y", "%m-%d-%Y", "%Y-%m-%d", "%Y-%d-%m",
    "%d%m%Y", "%m%d%Y", "%Y%m%d",
]

@dataclass
class ConfiguracionImportacion:
    """
    Todo lo que puede variar de una empresa/banco a otro. Esto se guarda
    en la base de datos por empresa (o incluso por banco) y se le pasa
    al importador; nunca se hardcodea.
    """
    formato: str
    columna_fecha: str

    # columnas_separadas
    columna_ingresos: Optional[str] = None
    columna_egresos: Optional[str] = None

    # columna_con_signo / valor_absoluto_y_tipo
    columna_importe: Optional[str] = None

    # valor_absoluto_y_tipo
    columna_tipo: Optional[str] = None
    palabras_ingreso: list = field(default_factory=lambda: ["+", "ingreso", "deposito", "abono"])
    palabras_egreso: list = field(default_factory=lambda: ["-", "egreso", "retiro", "cargo"])

    # columnas que se concatenan para armar la descripción (en orden)
    columnas_descripcion: list = field(default_factory=list)
    columnas_descripcion_ingresos: list = field(default_factory=list)
    columnas_descripcion_egresos: list = field(default_factory=list)

    # columnas opcionales si el banco las trae en el estado de cuenta
    columna_rfc_contraparte: Optional[str] = None
    columna_rfc_ingresos: Optional[str] = None
    columna_rfc_egresos: Optional[str] = None
    columna_cuenta_contraparte: Optional[str] = None
    columna_referencia: Optional[str] = None

    # Número de factura: casi ningún estado de cuenta bancario lo trae de
    # origen (normalmente se captura a mano al clasificar el movimiento,
    # o se cruza después contra el XML). Si el banco sí lo trae en alguna
    # columna, se puede mapear aquí; si no, queda vacío y se llena después.
    columna_numero_factura: Optional[str] = None

    # Muchos bancos (BBVA, Banamex...) meten varias filas de logo/membrete
    # antes de la fila real de encabezados. 0 = la primera fila es el
    # encabezado (comportamiento clásico); si el encabezado real está más
    # abajo, aquí se indica en qué fila (0-based) está.
    fila_encabezado: int = 0

def parsear_fecha(valor) -> Optional[datetime]:
    """Igual de tolerante que la versión vieja: prueba varios formatos
    antes de rendirse."""
    if pd.isna(valor):
        return None
    if isinstance(valor, datetime):
        return valor
    if hasattr(valor, "to_pydatetime"):  # pandas.Timestamp
        return valor.to_pydatetime()

    texto = str(valor)
    for fmt in FORMATOS_FECHA_SOPORTADOS:
        try:
            return datetime.strptime(texto, fmt)
        except ValueError:
            continue

    solo_numeros = re.sub(r"[^0-9]", "", texto)
    if len(solo_numeros) == 8:
        for fmt in ["%d%m%Y", "%m%d%Y", "%Y%m%d"]:
            try:
                return datetime.strptime(solo_numeros, fmt)
            except ValueError:
                continue

    return None

def _armar_descripcion(fila, columnas_descripcion: list) -> str:
    partes = []
    for col in columnas_descripcion:
        valor = fila.get(col)
        if valor is not None and not pd.isna(valor):
            partes.append(str(valor).strip())
    return " ".join(partes).strip()

PALABRAS_ENCABEZADO = [
    "fecha", "concepto", "descripcion", "descripción", "referencia",
    "cargo", "abono", "deposito", "depósito", "retiro", "importe",
    "monto", "saldo", "movimiento", "cuenta", "tipo",
]

def detectar_fila_encabezado(ruta_archivo: str, nombre_hoja: str = None) -> tuple:
    try:
        with pd.ExcelFile(ruta_archivo) as xls:
            df = xls.parse(nombre_hoja or xls.sheet_names[0], header=None, nrows=40)
        return detectar_fila_y_columnas(df)
    except:
        return 0, {}

def listar_hojas(ruta_archivo: str) -> list[str]:
    # Importante: pd.ExcelFile mantiene el archivo abierto hasta que se
    # cierra explícitamente. En Windows eso bloquea el archivo para
    # cualquier operación posterior (ej. moverlo a su carpeta final) si
    # no se cierra aquí mismo — por eso el `with`.
    with pd.ExcelFile(ruta_archivo) as xls:
        return xls.sheet_names

def vista_previa_cruda(ruta_archivo: str, nombre_hoja: str = None, filas: int = 20) -> dict:
    """
    Lee las primeras filas SIN asumir dónde está el encabezado (header=None),
    para que el usuario vea el archivo tal cual es y pueda decidir en qué
    fila está la tabla real. Muchos bancos (BBVA, Banamex) meten varias
    filas de logo/membrete/resumen de cuenta antes de la tabla de
    movimientos; sin esto, la vista previa mostraba esas filas como si
    fueran encabezados y no servía de nada.

    Regresa filas ya numeradas (1-based, como las ve el usuario en Excel)
    para que la fila que elija como encabezado se pueda mapear de vuelta.
    """
    with pd.ExcelFile(ruta_archivo) as xls:
        df = xls.parse(nombre_hoja or xls.sheet_names[0], header=None, nrows=filas, dtype=str)

    filas_crudas = []
    for i, (_, fila) in enumerate(df.iterrows(), start=1):
        valores = ["" if pd.isna(v) else str(v) for v in fila]
        filas_crudas.append({"numero": i, "valores": valores})

    total_columnas = len(df.columns)
    return {"filas": filas_crudas, "total_columnas": total_columnas}

def vista_previa(ruta_archivo: str, nombre_hoja: str = None, fila_encabezado: int = 0,
                  filas: int = 10) -> dict:
    """Lee las primeras filas YA con la fila de encabezado correcta
    aplicada, para armar el formulario de mapeo de columnas."""
    with pd.ExcelFile(ruta_archivo) as xls:
        df = xls.parse(nombre_hoja or xls.sheet_names[0], header=fila_encabezado, nrows=filas)

    columnas = [str(c) for c in df.columns]
    filas_preview = []
    for _, fila in df.iterrows():
        filas_preview.append([
            "" if pd.isna(v) else (v.strftime("%d/%m/%Y") if hasattr(v, "strftime") else str(v))
            for v in fila
        ])

    return {"columnas": columnas, "filas": filas_preview}

def procesar_dataframe(df: pd.DataFrame, config: ConfiguracionImportacion) -> dict:
    """
    Lee un archivo Excel de estado de cuenta y regresa una lista de
    movimientos normalizados, listos para pasar por rule_engine.py.

    Movimientos sin fecha válida se ignoran (igual que en el sistema
    viejo), y se reporta cuántos se saltaron.
    """

    movimientos = []
    filas_saltadas = 0

    for idx, fila in df.iterrows():
        fecha = parsear_fecha(fila.get(config.columna_fecha))
        if fecha is None:
            filas_saltadas += 1
            continue

        tipo = None
        monto = 0.0

        if config.formato == FORMATO_COLUMNAS_SEPARADAS:
            val_ingreso = fila.get(config.columna_ingresos)
            val_egreso = fila.get(config.columna_egresos)
            tiene_ingreso = val_ingreso is not None and not pd.isna(val_ingreso) and val_ingreso != 0
            if tiene_ingreso:
                tipo = "ingreso"
                monto = round(float(val_ingreso), 2)
            else:
                tipo = "egreso"
                monto = round(float(val_egreso) if val_egreso and not pd.isna(val_egreso) else 0.0, 2)

        elif config.formato == FORMATO_COLUMNA_CON_SIGNO:
            valor = fila.get(config.columna_importe)
            if valor is None or pd.isna(valor):
                filas_saltadas += 1
                continue
            valor = round(float(valor), 2)
            tipo = "ingreso" if valor > 0 else "egreso"
            monto = abs(valor)

        elif config.formato == FORMATO_VALOR_ABSOLUTO_Y_TIPO:
            valor = fila.get(config.columna_importe)
            if valor is None or pd.isna(valor):
                filas_saltadas += 1
                continue
            monto = abs(round(float(valor), 2))

            texto_tipo = str(fila.get(config.columna_tipo, "")).strip().lower()
            tipo = "egreso"  # por defecto, igual que el sistema viejo
            for palabra in config.palabras_ingreso:
                if palabra.lower() in texto_tipo:
                    tipo = "ingreso"
                    break
            for palabra in config.palabras_egreso:
                if palabra.lower() in texto_tipo:
                    tipo = "egreso"
                    break
        else:
            raise ValueError(f"Formato de importación desconocido: {config.formato}")

        if monto == 0:
            filas_saltadas += 1
            continue

        if tipo == "ingreso":
            cols_desc = config.columnas_descripcion_ingresos or config.columnas_descripcion
            col_rfc = config.columna_rfc_ingresos or config.columna_rfc_contraparte
        else:
            cols_desc = config.columnas_descripcion_egresos or config.columnas_descripcion
            col_rfc = config.columna_rfc_egresos or config.columna_rfc_contraparte

        descripcion = _armar_descripcion(fila, cols_desc) or "(sin descripción)"

        cuenta_contraparte = (
            str(fila.get(config.columna_cuenta_contraparte)).strip()
            if config.columna_cuenta_contraparte and not pd.isna(fila.get(config.columna_cuenta_contraparte, None))
            else None
        )
        if not cuenta_contraparte:
            cuenta_contraparte = _detectar_cuenta_en_descripcion(descripcion)

        movimiento = {
            "fecha": fecha,
            "descripcion": descripcion,
            "tipo": tipo,
            "total": monto,
            "fila_original": idx,
            "rfc_contraparte": (
                str(fila.get(col_rfc)).strip()
                if col_rfc and not pd.isna(fila.get(col_rfc, None))
                else _detectar_rfc_en_descripcion(descripcion)
            ),
            "cuenta_bancaria_contraparte": cuenta_contraparte,
            "referencia_bancaria": (
                str(fila.get(config.columna_referencia)).strip()
                if config.columna_referencia and not pd.isna(fila.get(config.columna_referencia, None))
                else None
            ),
            "numero_factura": (
                str(fila.get(config.columna_numero_factura)).strip()
                if config.columna_numero_factura and not pd.isna(fila.get(config.columna_numero_factura, None))
                else ""
            ),
        }
        movimientos.append(movimiento)

    return {
        "movimientos": movimientos,
        "total_filas": len(df),
        "filas_importadas": len(movimientos),
        "filas_saltadas": filas_saltadas,
    }

def importar_excel(ruta_archivo: str, config: ConfiguracionImportacion,
                   nombre_hoja: Optional[str] = None) -> dict:
    """
    Lee un archivo Excel de estado de cuenta y regresa el dict de procesamiento.
    """
    with pd.ExcelFile(ruta_archivo) as xls:
        df = xls.parse(nombre_hoja or xls.sheet_names[0], header=config.fila_encabezado)
    return procesar_dataframe(df, config)

