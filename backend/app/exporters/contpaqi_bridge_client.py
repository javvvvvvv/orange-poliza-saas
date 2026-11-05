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
Cliente único hacia ContpaqiBridge. Antes había dos: uno con `urllib.request`
en contpaqi_sdk_exporter.py y otro con `requests` en catalogo_repo.py, cada
uno con su propia URL quemada y su propio header de Host falso. Ahora hay un
solo cliente, con reintentos y health-check, y la URL sale de la variable de
entorno CONTPAQI_BRIDGE_URL (default: http://host.docker.internal:5005,
que sigue siendo el valor correcto para Docker Desktop en Windows/Mac).

El bridge nuevo (contpaqi-bridge/, Kestrel) ya no requiere el header de Host
falso — eso era un parche para el HttpListener anterior.
"""
from __future__ import annotations

import os
import time
from typing import Any

import requests

_URL_BASE = os.environ.get("CONTPAQI_BRIDGE_URL", "http://host.docker.internal:5005").rstrip("/")
_TIMEOUT_SEGUNDOS = float(os.environ.get("CONTPAQI_BRIDGE_TIMEOUT", "300"))
_REINTENTOS = int(os.environ.get("CONTPAQI_BRIDGE_REINTENTOS", "2"))
_ESPERA_ENTRE_REINTENTOS = 1.5
_API_KEY = os.environ.get("CONTPAQI_BRIDGE_API_KEY", "orange_bridge_secret_2026").strip()

class ErrorBridgeContpaqi(Exception):
    """El bridge no respondió, o respondió pero marcó exito=false."""

def _headers() -> dict[str, str]:
    headers = {}
    if _API_KEY:
        headers["X-Bridge-ApiKey"] = _API_KEY
    return headers

def bridge_disponible() -> bool:
    """Health-check rápido; úsalo antes de mostrar una acción en la UI que
    dependa del bridge, para avisar al usuario ANTES de que intente exportar."""
    try:
        resp = requests.get(f"{_URL_BASE}/salud", headers=_headers(), timeout=5)
        return resp.status_code == 200
    except requests.RequestException:
        return False

def _get_con_reintentos(ruta: str, timeout: float | None = None, reintentos: int | None = None) -> dict[str, Any]:
    t = timeout if timeout is not None else _TIMEOUT_SEGUNDOS
    r = reintentos if reintentos is not None else _REINTENTOS
    ultimo_error: Exception | None = None
    for intento in range(r + 1):
        try:
            resp = requests.get(f"{_URL_BASE}{ruta}", headers=_headers(), timeout=t)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            ultimo_error = e
            if intento < r:
                time.sleep(_ESPERA_ENTRE_REINTENTOS)

    raise ErrorBridgeContpaqi(
        f"No se pudo conectar al puente en {_URL_BASE} tras {r + 1} intento(s) "
        f"(¿está corriendo iniciar_puente_contpaqi.bat?): {ultimo_error}"
    )

def _post_con_reintentos(ruta: str, payload: dict[str, Any]) -> dict[str, Any]:
    ultimo_error: Exception | None = None
    for intento in range(_REINTENTOS + 1):
        try:
            resp = requests.post(f"{_URL_BASE}{ruta}", json=payload, headers=_headers(), timeout=_TIMEOUT_SEGUNDOS)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            ultimo_error = e
            if intento < _REINTENTOS:
                time.sleep(_ESPERA_ENTRE_REINTENTOS)

    raise ErrorBridgeContpaqi(
        f"No se pudo conectar al puente en {_URL_BASE} tras {_REINTENTOS + 1} intento(s) "
        f"(¿está corriendo iniciar_puente_contpaqi.bat?): {ultimo_error}"
    )

def exportar_polizas(payload: dict[str, Any]) -> dict[str, Any]:
    return _post_con_reintentos("/", payload)

def listar_cuentas(payload: dict[str, Any]) -> dict[str, Any]:
    return _post_con_reintentos("/cuentas/listar", payload)

def listar_empresas() -> dict[str, Any]:
    return _get_con_reintentos('/empresas/listar', timeout=20.0, reintentos=0)

def crear_cuenta(payload: dict[str, Any]) -> dict[str, Any]:
    return _post_con_reintentos('/cuentas/crear', payload)
