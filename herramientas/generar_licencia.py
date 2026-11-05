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
Emisor de licencias de Orange Poliza Engine — herramienta de Javier/Orange
Crew, NO se distribuye a clientes ni se sube al servidor de producción.

Modo interactivo (recomendado, lo usa emitir_licencia.bat):
    python3 generar_licencia.py

    Lista las empresas que ya existen en la base de datos y deja
    marcar/desmarcar cuáles quedan autorizadas. Si no se puede conectar
    a la base (falta webapp/.env, servidor apagado, etc.) cae solo a
    modo manual: pide los nombres de empresa escritos a mano.

Modo directo (sin preguntas, para scripts):
    python3 generar_licencia.py --empresa "Cliente X SA de CV" \
        --empresa "Cliente Y" --expira 2027-12-31 --titular "Cliente X" \
        --salida ../licencia/orange.lic

Una empresa "*" autoriza cualquier empresa (útil para tu propio ambiente
de desarrollo/demos internos, nunca para un cliente final).
"""

import argparse
import base64
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend", "app", "core"))
from licencia import _firmar, _normalizar, _cargar_archivo  # reutiliza la misma llave/firma

_RUTA_DEFECTO = os.path.join(os.path.dirname(__file__), "..", "licencia", "orange.lic")

def emitir(empresas: list, expira: str, titular: str, salida: str) -> None:
    payload = json.dumps({"empresas": empresas, "expira": expira, "titular": titular},
                          separators=(",", ":")).encode()
    firma = _firmar(payload)
    contenido = base64.b64encode(payload).decode() + "." + firma

    os.makedirs(os.path.dirname(salida), exist_ok=True)
    with open(salida, "w", encoding="utf-8") as f:
        f.write(contenido)

    print(f"\nLicencia emitida en: {salida}")
    print(f"  Empresas autorizadas: {', '.join(empresas)}")
    print(f"  Expira: {expira}")
    print(f"  Titular: {titular}")

def _empresas_autorizadas_actuales(ruta: str) -> set:
    """Lee la licencia vigente (si existe) solo para mostrar el estado
    actual en la lista; si está corrupta o no existe, no es un error
    aquí, simplemente se muestra todo como no autorizado."""
    try:
        datos = _cargar_archivo(ruta, verificar_firma=False)
        return {_normalizar(e) for e in datos.get("empresas", [])}
    except Exception:
        return set()

def _listar_empresas_bd() -> list:
    """Regresa [(id, nombre), ...] desde la base, o levanta una
    excepción si no se pudo conectar (el llamador decide el fallback)."""
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "webapp"))
    from db import get_connection  # import perezoso: solo si hace falta

    con = get_connection()
    filas = con.execute("SELECT id, nombre FROM empresas ORDER BY nombre").fetchall()
    con.close()
    return [(f["id"], f["nombre"]) for f in filas]

def _modo_interactivo_con_bd(empresas_bd: list) -> list:
    autorizadas_actuales = _empresas_autorizadas_actuales(_RUTA_DEFECTO)
    marcadas = {nombre for (_id, nombre) in empresas_bd if _normalizar(nombre) in autorizadas_actuales}

    while True:
        print("\nEmpresas en el sistema:")
        for i, (_id, nombre) in enumerate(empresas_bd, start=1):
            marca = "[X]" if nombre in marcadas else "[ ]"
            print(f"  {i:>2}) {marca} {nombre}")
        print("\nEscribe los números a alternar, separados por espacio o coma")
        print("(ej. 1 3 5), 'todos', 'ninguno', o deja vacío y Enter para continuar.")
        resp = input("> ").strip().lower()

        if resp == "":
            break
        if resp == "todos":
            marcadas = {nombre for (_id, nombre) in empresas_bd}
            continue
        if resp == "ninguno":
            marcadas = set()
            continue

        for tok in resp.replace(",", " ").split():
            if not tok.isdigit():
                continue
            idx = int(tok) - 1
            if 0 <= idx < len(empresas_bd):
                nombre = empresas_bd[idx][1]
                if nombre in marcadas:
                    marcadas.remove(nombre)
                else:
                    marcadas.add(nombre)

    return sorted(marcadas)

def _modo_manual() -> list:
    print("\nNo se pudo leer la lista de empresas de la base de datos,")
    print("así que captúralas a mano (nombre exactamente como está en")
    print("Configuración de la empresa en el sistema).")
    empresas = []
    while True:
        nombre = input("Nombre de empresa a autorizar (vacío + Enter para terminar): ").strip()
        if not nombre:
            break
        empresas.append(nombre)
    return empresas

def _modo_interactivo() -> None:
    try:
        empresas_bd = _listar_empresas_bd()
        if not empresas_bd:
            raise RuntimeError("La base de datos no tiene empresas capturadas todavía.")
        seleccion = _modo_interactivo_con_bd(empresas_bd)
    except Exception as e:
        print(f"\nAviso: {e}")
        seleccion = _modo_manual()

    if not seleccion:
        print("\nNo quedó ninguna empresa autorizada. No se emitió ninguna licencia.")
        return

    expira = input("\nFecha de expiración (AAAA-MM-DD, ej. 2027-12-31): ").strip()
    titular = input("Nombre del cliente/titular de la licencia: ").strip()
    emitir(seleccion, expira, titular, _RUTA_DEFECTO)

if __name__ == "__main__":
    if len(sys.argv) == 1:
        _modo_interactivo()
    else:
        parser = argparse.ArgumentParser(description=__doc__)
        parser.add_argument("--empresa", action="append", required=True, dest="empresas",
                             help="Nombre de empresa autorizada (repetir la bandera para varias)")
        parser.add_argument("--expira", required=True, help="YYYY-MM-DD")
        parser.add_argument("--titular", required=True)
        parser.add_argument("--salida", default=_RUTA_DEFECTO)
        args = parser.parse_args()
        emitir(args.empresas, args.expira, args.titular, args.salida)

