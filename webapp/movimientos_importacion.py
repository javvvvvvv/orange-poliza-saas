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

import hashlib
from collections import Counter

import _rutas  # noqa: F401  (deja backend/app/core y exporters en sys.path)

from db import get_connection
from rule_engine import normalizar_texto
from formula_engine import redondear_moneda
from movimientos_comun import TAMANO_LOTE


class IdsInsertados(list):
    """Lista de ids insertados; `duplicados` cuenta los movimientos que ya existían."""
    duplicados: int = 0


def _huella(empresa_id, banco_id, fecha, descripcion_normalizada, tipo, total, referencia, ocurrencia) -> str:
    """Identidad estable de un movimiento bancario.

    `ocurrencia` distingue dos cargos idénticos el mismo día dentro de un archivo,
    pero permite reconocer que el mismo movimiento ya se importó en otro archivo.
    """
    partes = [str(empresa_id), str(banco_id or 0), fecha, descripcion_normalizada,
              str(tipo), str(total), str(referencia or ""), str(ocurrencia)]
    return hashlib.sha256("|".join(partes).encode("utf-8")).hexdigest()


def _huellas_existentes(con, empresa_id, huellas) -> set:
    existentes = set()
    for inicio in range(0, len(huellas), TAMANO_LOTE):
        lote = huellas[inicio:inicio + TAMANO_LOTE]
        marcadores = ",".join(["?"] * len(lote))
        filas = con.execute(
            f"SELECT huella FROM movimientos WHERE empresa_id = ? AND huella IN ({marcadores})",
            (empresa_id, *lote),
        ).fetchall()
        existentes.update(f["huella"] for f in filas)
    return existentes


def insertar_movimientos(empresa_id, documento_id, movimientos):
    """Inserta movimientos nuevos y omite los que ya fueron importados antes."""
    with get_connection() as con:
        banco_id = None
        if documento_id is not None:
            documento = con.execute(
                "SELECT banco_id FROM documentos_importados WHERE id = ? AND empresa_id = ?",
                (documento_id, empresa_id),
            ).fetchone()
            banco_id = documento["banco_id"] if documento else None

        preparados = []
        vistos = Counter()
        for mov in movimientos:
            fecha = (
                mov["fecha"].strftime("%Y-%m-%d") if hasattr(mov["fecha"], "strftime")
                else str(mov["fecha"])[:10]
            )
            descripcion_normalizada = normalizar_texto(mov["descripcion"])
            total = redondear_moneda(mov["total"])
            referencia = mov.get("referencia_bancaria")
            clave = (fecha, descripcion_normalizada, mov["tipo"], total, referencia)
            vistos[clave] += 1
            huella = _huella(empresa_id, banco_id, fecha, descripcion_normalizada,
                             mov["tipo"], total, referencia, vistos[clave])
            preparados.append((huella, fecha, descripcion_normalizada, total, mov))

        existentes = _huellas_existentes(con, empresa_id, [p[0] for p in preparados])
        ids = IdsInsertados()
        cur = con.cursor()
        for huella, fecha, descripcion_normalizada, total, mov in preparados:
            if huella in existentes:
                ids.duplicados += 1
                continue
            cur.execute(
                """INSERT INTO movimientos
                   (empresa_id, documento_id, fecha, descripcion, descripcion_normalizada,
                    tipo, total, rfc_contraparte, cuenta_bancaria_contraparte,
                    referencia_bancaria, numero_factura, fila_original, estado_clasificacion, huella)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pendiente', ?)""",
                (
                    empresa_id,
                    documento_id,
                    fecha,
                    mov["descripcion"],
                    descripcion_normalizada,
                    mov["tipo"],
                    total,
                    mov.get("rfc_contraparte"),
                    mov.get("cuenta_bancaria_contraparte"),
                    mov.get("referencia_bancaria"),
                    mov.get("numero_factura") or "",
                    mov.get("fila_original"),
                    huella,
                ),
            )
            ids.append(cur.lastrowid)
    return ids
