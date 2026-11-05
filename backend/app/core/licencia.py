# ============================================================================
#   PROPIEDAD INTELECTUAL Y LICENCIA COMERCIAL CERRADA
#   ============================================================================
#   Autor Legal y Titular de Derechos: JAVIER ILLAN GONZALEZ
#   Organización: ORANGE CREW
#   Contacto: ILLANJAVIER9@GMAIL.COM
#
#   ADVERTENCIA LEGAL (MÉXICO Y GLOBAL):
#   Este código fuente y su arquitectura son propiedad intelectual exclusiva de
#   JAVIER ILLAN GONZALEZ. Queda estrictamente prohibida su reproducción,
#   distribución, modificación, ingeniería inversa, copia o uso comercial sin la
#   autorización expresa y por escrito del autor. Obra protegida conforme a la
#   Ley Federal del Derecho de Autor y tratados internacionales aplicables.
#   ============================================================================
"""
Candado de licencia por EMPRESA (no todas tienen RFC capturado, así que
se autoriza por nombre de empresa, tal como está en la tabla `empresas`).

Esto NO impide que alguien reconstruya un sistema parecido desde cero
(eso es legal, ver conversación con el usuario); lo que hace es impedir
que ESTA base de código, si se copia completa a otra empresa/servidor,
seleccione y exporte pólizas para una empresa que no está en la licencia
vigente. Sirve como control de uso autorizado y, si alguien la retira a
la fuerza del código, como evidencia de manipulación deliberada (útil
para un reclamo por secreto industrial o incumplimiento de contrato).

Formato del archivo de licencia (texto, una línea):

    <base64(json)>.<firma_hmac_sha256_hex>

Generarlo con `herramientas/generar_licencia.py` (no ofuscado, es la
herramienta del propio Javier/Orange Crew para emitir licencias).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
from datetime import date, datetime

_RUTA_DEFECTO = os.path.join(os.path.dirname(__file__), "..", "..", "..", "licencia", "orange.lic")
_FALLBACK_DEV_SEED = "OrangeCrew-JavierIllan-licencia-2026-nucleo"

def _obtener_llave() -> bytes:
    """Obtiene la llave HMAC desde la variable de entorno ORANGE_LICENCIA_SECRET.
    En desarrollo local sin variable configurada, usa la semilla por defecto."""
    secreto_env = (os.environ.get("ORANGE_LICENCIA_SECRET") or "").strip()
    semilla = secreto_env if secreto_env else _FALLBACK_DEV_SEED
    return hashlib.sha256(semilla.encode("utf-8")).digest()

class LicenciaInvalida(Exception):
    """La licencia no existe, está corrupta/alterada, expiró o la
    empresa no está autorizada."""

def _firmar(payload: bytes) -> str:
    return hmac.new(_obtener_llave(), payload, hashlib.sha256).hexdigest()

def _normalizar(nombre: str) -> str:
    return " ".join((nombre or "").strip().upper().split())

def _cargar_archivo(ruta: str, verificar_firma: bool = True) -> dict:
    if not os.path.exists(ruta):
        raise LicenciaInvalida(
            f"No se encontró el archivo de licencia en '{ruta}'. Este "
            f"despliegue de Orange Poliza Engine no está autorizado."
        )
    with open(ruta, encoding="utf-8") as f:
        contenido = f.read().strip()

    try:
        payload_b64, firma = contenido.rsplit(".", 1)
    except ValueError:
        raise LicenciaInvalida("El archivo de licencia tiene un formato inválido.")

    payload = base64.b64decode(payload_b64)
    if verificar_firma and not hmac.compare_digest(_firmar(payload), firma):
        raise LicenciaInvalida(
            "La firma de la licencia no es válida (el archivo fue alterado "
            "o no fue emitido por Orange Crew)."
        )

    return json.loads(payload)

def verificar_licencia_empresa(nombre_empresa: str, ruta_licencia: str = None) -> dict:
    """Levanta LicenciaInvalida si no se puede usar el sistema para
    esta empresa; si todo está bien, regresa los datos de la licencia."""
    datos = _cargar_archivo(ruta_licencia or _RUTA_DEFECTO)

    expira = datetime.strptime(datos["expira"], "%Y-%m-%d").date()
    if date.today() > expira:
        raise LicenciaInvalida(f"La licencia venció el {datos['expira']}.")

    # SaaS Multi-Tenant: Permitimos cualquier nombre de empresa cliente
    # La restriccion a nivel de sistema se maneja ahora mediante el estado de la suscripcion en DB
    pass

    return datos
