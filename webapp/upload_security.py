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
import os
import time
import uuid
import shutil
import zipfile
from werkzeug.utils import secure_filename

EXTENSIONES_ESTADO_CUENTA = frozenset({".xlsx", ".xls", ".csv", ".pdf"})
EXTENSIONES_CATALOGO = frozenset({".xlsx", ".xls", ".csv"})
EXTENSIONES_CFDI = frozenset({".xml", ".zip"})

MAX_ARCHIVOS_ZIP = 500
MAX_BYTES_TOTALES_ZIP = 100 * 1024 * 1024  # 100 MB descomprimidos
MAX_BYTES_POR_XML = 10 * 1024 * 1024       # 10 MB por XML individual
MAX_RATIO_COMPRESION_ZIP = 100.0

class ErrorArchivoInseguro(ValueError):
    """Se lanza cuando un archivo subido o miembro de un ZIP es inválido o inseguro."""

def sanitizar_nombre_archivo(
    nombre_original: str,
    extensiones_permitidas=None,
    prefijo_fallback: str = "archivo",
) -> str:
    """
    Sanitiza un nombre de archivo con `secure_filename`, elimina cualquier
    componente de ruta (`..`, `/`, `\\`) y valida su extensión contra una
    lista blanca si se especifica.
    """
    if not nombre_original or not str(nombre_original).strip():
        raise ErrorArchivoInseguro("El nombre del archivo está vacío.")

    base_raw = os.path.basename(str(nombre_original).replace("\\", "/"))
    _, ext_raw = os.path.splitext(base_raw)
    ext = ext_raw.lower()

    if extensiones_permitidas is not None:
        permitidas_norm = {e.lower() if e.startswith(".") else f".{e.lower()}" for e in extensiones_permitidas}
        if not ext or ext not in permitidas_norm:
            lista_txt = ", ".join(sorted(permitidas_norm))
            raise ErrorArchivoInseguro(
                f"Extensión de archivo no permitida ('{ext or 'sin extensión'}'). Permitidas: {lista_txt}."
            )

    seguro = secure_filename(base_raw)
    if not seguro or seguro.startswith("."):
        seguro = f"{prefijo_fallback}_{uuid.uuid4().hex[:8]}{ext}"
    else:
        _, ext_seguro = os.path.splitext(seguro)
        if not ext_seguro and ext:
            seguro = f"{seguro}{ext}"

    return seguro

def ruta_segura_en_directorio(
    directorio_base: str,
    nombre_original: str,
    extensiones_permitidas=None,
    prefijo_unico: bool = False,
) -> str:
    """
    Construye una ruta absoluta dentro de `directorio_base` garantizando que
    el nombre esté sanitizado con `secure_filename` y que no exista Path Traversal.
    """
    nombre_limpio = sanitizar_nombre_archivo(
        nombre_original,
        extensiones_permitidas=extensiones_permitidas,
    )
    if prefijo_unico:
        nombre_limpio = f"{uuid.uuid4().hex[:12]}_{nombre_limpio}"

    dir_abs = os.path.abspath(directorio_base)
    os.makedirs(dir_abs, exist_ok=True)
    ruta_abs = os.path.abspath(os.path.join(dir_abs, nombre_limpio))

    if os.path.commonpath([dir_abs, ruta_abs]) != dir_abs or ruta_abs == dir_abs:
        raise ErrorArchivoInseguro("Intento de escritura fuera del directorio permitido bloqueado.")

    return ruta_abs

def extraer_xmls_de_zip_seguro(
    ruta_zip: str,
    directorio_destino: str,
    max_archivos: int = MAX_ARCHIVOS_ZIP,
    max_bytes_totales: int = MAX_BYTES_TOTALES_ZIP,
    max_bytes_por_archivo: int = MAX_BYTES_POR_XML,
    max_ratio_compresion: float = MAX_RATIO_COMPRESION_ZIP,
) -> list[str]:
    """
    Extrae únicamente archivos `.xml` desde un archivo `.zip` validando:
      - Que no contenga rutas relativas maliciosas (`..` / Zip Slip)
      - Número máximo de archivos (`max_archivos`)
      - Tamaño máximo descomprimido total y por archivo (`Zip Bomb`)
      - Ratio de compresión máximo
    """
    dir_abs = os.path.abspath(directorio_destino)
    os.makedirs(dir_abs, exist_ok=True)
    rutas_extraidas = []
    bytes_totales = 0

    try:
        zf_ctx = zipfile.ZipFile(ruta_zip, "r")
    except zipfile.BadZipFile as e:
        raise ErrorArchivoInseguro(f"El archivo ZIP está dañado o no es válido: {e}")

    with zf_ctx as zf:
        infos = zf.infolist()
        if len(infos) > max_archivos:
            raise ErrorArchivoInseguro(
                f"El archivo ZIP contiene demasiadas entradas ({len(infos)} > máximo {max_archivos})."
            )

        for idx, info in enumerate(infos):
            nombre_miembro = info.filename.replace("\\", "/")
            Partes = [p for p in nombre_miembro.split("/") if p]
            if ".." in Partes or nombre_miembro.startswith("/"):
                raise ErrorArchivoInseguro(
                    f"Ruta insegura detectada dentro del ZIP: '{info.filename}'."
                )

            if info.is_dir():
                continue

            if not nombre_miembro.lower().endswith(".xml"):
                continue

            if info.file_size > max_bytes_por_archivo:
                raise ErrorArchivoInseguro(
                    f"El XML '{os.path.basename(nombre_miembro)}' excede el tamaño máximo permitido "
                    f"({info.file_size} bytes > {max_bytes_por_archivo} bytes)."
                )

            if info.compress_size > 0:
                ratio = info.file_size / float(info.compress_size)
                if ratio > max_ratio_compresion and info.file_size > 1024 * 1024:
                    raise ErrorArchivoInseguro(
                        f"Ratio de compresión sospechoso ({ratio:.1f}x) en '{os.path.basename(nombre_miembro)}'."
                    )

            bytes_totales += info.file_size
            if bytes_totales > max_bytes_totales:
                raise ErrorArchivoInseguro(
                    f"El contenido descomprimido del ZIP excede el límite permitido ({max_bytes_totales // (1024 * 1024)} MB)."
                )

            nombre_seguro = sanitizar_nombre_archivo(
                os.path.basename(nombre_miembro),
                extensiones_permitidas={".xml"},
                prefijo_fallback=f"cfdi_{idx}",
            )
            ruta_salida = os.path.abspath(
                os.path.join(dir_abs, f"{idx:04d}_{nombre_seguro}")
            )
            if os.path.commonpath([dir_abs, ruta_salida]) != dir_abs:
                raise ErrorArchivoInseguro("Intento de Zip Slip bloqueado.")

            leidos_archivo = 0
            with zf.open(info, "r") as src, open(ruta_salida, "wb") as dst:
                while True:
                    bloque = src.read(65536)
                    if not bloque:
                        break
                    leidos_archivo += len(bloque)
                    if leidos_archivo > max_bytes_por_archivo:
                        raise ErrorArchivoInseguro(
                            f"El archivo '{nombre_seguro}' excedió el tamaño declarado al descomprimir."
                        )
                    dst.write(bloque)

            rutas_extraidas.append(ruta_salida)

    return rutas_extraidas

def limpiar_temporales_antiguos(directorio_tmp_base: str, max_edad_segundos: int = 3600) -> int:
    """
    Elimina subcarpetas o archivos dentro de `directorio_tmp_base` cuya última
    modificación sea más antigua que `max_edad_segundos`.
    """
    if not os.path.isdir(directorio_tmp_base):
        return 0
    ahora = time.time()
    eliminados = 0
    for nombre in os.listdir(directorio_tmp_base):
        ruta = os.path.join(directorio_tmp_base, nombre)
        try:
            mtime = os.path.getmtime(ruta)
            if (ahora - mtime) > max_edad_segundos:
                if os.path.isdir(ruta):
                    shutil.rmtree(ruta, ignore_errors=True)
                else:
                    os.remove(ruta)
                eliminados += 1
        except OSError:
            pass
    return eliminados
