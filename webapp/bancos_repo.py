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

def listar_bancos(empresa_id):
    with get_connection() as con:
        filas = con.execute(
            "SELECT * FROM bancos WHERE empresa_id = ? ORDER BY nombre", (empresa_id,)
        ).fetchall()
        return filas

def crear_banco(empresa_id, nombre, cuenta_contable, moneda="MXN"):
    with get_connection() as con:
        con.execute(
            "INSERT INTO bancos (empresa_id, nombre, cuenta_contable, moneda) VALUES (?, ?, ?, ?)",
            (empresa_id, nombre, cuenta_contable, moneda),
        )
        con.commit()

def eliminar_banco(banco_id, empresa_id=None) -> bool:
    with get_connection() as con:
        if empresa_id is not None:
            cur = con.execute(
                "DELETE FROM bancos WHERE id = ? AND empresa_id = ?",
                (banco_id, empresa_id),
            )
        else:
            cur = con.execute("DELETE FROM bancos WHERE id = ?", (banco_id,))
        con.commit()
        return cur.rowcount > 0

def obtener_empresa(empresa_id):
    with get_connection() as con:
        fila = con.execute("SELECT * FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
        return fila

def actualizar_configuracion_iva(empresa_id, datos):
    with get_connection() as con:
        con.execute(
            """UPDATE empresas SET
               tasa_iva = ?, cuenta_iva_acreditable = ?, cuenta_iva_por_acreditar = ?,
               cuenta_iva_trasladado = ?, cuenta_iva_por_trasladar = ?,
               cuenta_complementaria_ingresos = ?, cuenta_complementaria_egresos = ?,
               cuenta_dif_cambiaria = ?, retenciones_activas = ?,
               cuenta_retencion_iva = ?, cuenta_retencion_isr = ?, base_datos_contpaqi = ?
               WHERE id = ?""",
            (
                datos["tasa_iva"], datos.get("cuenta_iva_acreditable"),
                datos.get("cuenta_iva_por_acreditar"), datos.get("cuenta_iva_trasladado"),
                datos.get("cuenta_iva_por_trasladar"), datos.get("cuenta_complementaria_ingresos"),
                datos.get("cuenta_complementaria_egresos"), datos.get("cuenta_dif_cambiaria"),
                1 if datos.get("retenciones_activas") else 0,
                datos.get("cuenta_retencion_iva"), datos.get("cuenta_retencion_isr"),
                datos.get("base_datos_contpaqi"), empresa_id,
            ),
        )
        con.commit()
