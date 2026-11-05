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
Escritor de archivos .xls "de verdad" (formato binario BIFF8 / Excel 97-2003).

Contpaqi importa pólizas masivas en .xls clásico, no en .xlsx. En este
entorno no hay paquetes como `xlwt` disponibles (y no hay red para
instalarlos), pero SÍ está instalado LibreOffice, así que resolvemos el
problema igual que lo haría cualquier proceso de conversión de servidor:

    1. Armamos el archivo con openpyxl (que solo sabe escribir .xlsx)
    2. Lo convertimos a .xls real invocando `soffice --headless --convert-to xls`

Se probó que el round-trip conserva tipos correctamente: fechas siguen
siendo fechas, números siguen siendo números, y los códigos de cuenta
guardados como texto (ej. '20001000009000000') NO se convierten a
notación científica ni pierden ceros a la izquierda, que es el problema
más común al mover códigos de cuenta contable entre formatos de Excel.

En Claude Code, cuando tengamos red, esto se puede reemplazar por
`xlwt` directo (más rápido, sin depender de LibreOffice) sin cambiar la
interfaz de `guardar_como_xls()`.
"""

import os
import platform
import shutil
import subprocess
import tempfile

from openpyxl import Workbook

class ErrorConversionXls(Exception):
    pass

# Rutas típicas donde queda instalado LibreOffice cuando el instalador
# NO agrega soffice al PATH del sistema (el caso normal en Windows: el
# instalador de LibreOffice nunca toca el PATH, así que aunque esté
# instalado, `subprocess.run(["soffice", ...])` no lo encuentra a menos
# que se le dé la ruta completa).
_RUTAS_CANDIDATAS_WINDOWS = [
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
]
_RUTAS_CANDIDATAS_MAC = [
    "/Applications/LibreOffice.app/Contents/MacOS/soffice",
]

_ruta_soffice_cache = None

def _encontrar_soffice() -> str:
    """Busca el ejecutable de LibreOffice: primero en el PATH (funciona
    en Linux/Mac y en Windows si alguien lo agregó a mano), y si no,
    en las rutas de instalación típicas de cada sistema operativo."""
    global _ruta_soffice_cache
    if _ruta_soffice_cache:
        return _ruta_soffice_cache

    en_path = shutil.which("soffice") or shutil.which("soffice.exe")
    if en_path:
        _ruta_soffice_cache = en_path
        return en_path

    sistema = platform.system()
    candidatas = _RUTAS_CANDIDATAS_WINDOWS if sistema == "Windows" else (
        _RUTAS_CANDIDATAS_MAC if sistema == "Darwin" else []
    )
    for ruta in candidatas:
        if os.path.exists(ruta):
            _ruta_soffice_cache = ruta
            return ruta

    raise ErrorConversionXls(
        "No se encontró LibreOffice instalado. Se buscó en el PATH del "
        "sistema y en las rutas típicas de instalación "
        f"({', '.join(candidatas) if candidatas else 'ninguna conocida para este sistema operativo'}). "
        "Si ya lo instalaste, confirma que quedó en una de esas rutas, o "
        "agrega la carpeta 'program' de LibreOffice al PATH del sistema "
        "y reinicia la aplicación."
    )

def guardar_como_xls(workbook: Workbook, ruta_salida_xls: str, timeout_segundos: int = 60):
    """
    Guarda un Workbook de openpyxl como archivo .xls real en `ruta_salida_xls`.
    """
    ruta_salida_xls = os.path.abspath(ruta_salida_xls)
    os.makedirs(os.path.dirname(ruta_salida_xls), exist_ok=True)

    ruta_soffice = _encontrar_soffice()

    with tempfile.TemporaryDirectory() as tmp_dir:
        ruta_xlsx_temporal = os.path.join(tmp_dir, "temporal.xlsx")
        workbook.save(ruta_xlsx_temporal)

        resultado = subprocess.run(
            [
                ruta_soffice, "--headless", "--convert-to", "xls",
                "--outdir", tmp_dir, ruta_xlsx_temporal,
            ],
            capture_output=True, text=True, timeout=timeout_segundos,
        )

        ruta_xls_generado = os.path.join(tmp_dir, "temporal.xls")

        if resultado.returncode != 0 or not os.path.exists(ruta_xls_generado):
            raise ErrorConversionXls(
                f"LibreOffice se encontró en '{ruta_soffice}' pero la conversión falló.\n"
                f"stdout: {resultado.stdout}\nstderr: {resultado.stderr}"
            )

        shutil.copy(ruta_xls_generado, ruta_salida_xls)

    return ruta_salida_xls

