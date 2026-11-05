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
from db import get_connection

def listar(empresa_id, tipo_movimiento=None):
    with get_connection() as con:
        if tipo_movimiento:
            filas = con.execute(
                "SELECT * FROM reglas_generales WHERE empresa_id = ? AND tipo_movimiento = ? ORDER BY orden",
                (empresa_id, tipo_movimiento),
            ).fetchall()
        else:
            filas = con.execute(
                "SELECT * FROM reglas_generales WHERE empresa_id = ? ORDER BY tipo_movimiento, orden",
                (empresa_id,),
            ).fetchall()
        return filas

def agregar(empresa_id, tipo_movimiento, cuenta, naturaleza, formula, descripcion_linea=None):
    with get_connection() as con:
        cur = con.cursor()
        orden = cur.execute(
            "SELECT COALESCE(MAX(orden), -1) + 1 FROM reglas_generales WHERE empresa_id = ? AND tipo_movimiento = ?",
            (empresa_id, tipo_movimiento),
        ).fetchone()[0]
        cur.execute(
            """INSERT INTO reglas_generales
               (empresa_id, tipo_movimiento, orden, cuenta, naturaleza, formula, descripcion_linea)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (empresa_id, tipo_movimiento, orden, cuenta, naturaleza, formula, descripcion_linea),
        )
        con.commit()

def eliminar(linea_id, empresa_id=None) -> bool:
    with get_connection() as con:
        if empresa_id is not None:
            cur = con.execute(
                "DELETE FROM reglas_generales WHERE id = ? AND empresa_id = ?",
                (linea_id, empresa_id),
            )
        else:
            cur = con.execute("DELETE FROM reglas_generales WHERE id = ?", (linea_id,))
        con.commit()
        return cur.rowcount > 0

