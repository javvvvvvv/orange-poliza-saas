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
Repositorio de reglas para Orange Poliza Engine.

Esto es lo que hace que las reglas sean "configurables por datos" de
verdad: en vez de vivir como una lista de `Regla(...)` escrita a mano en
un script (como en los demos anteriores), viven en una base SQLite en
disco y se pueden agregar, quitar, activar/desactivar y editar en
cualquier momento, sin tocar código. Cuando pasemos a producción con
Claude Code, este mismo repositorio se vuelve una capa sobre PostgreSQL
(las tablas ya están definidas en database/schema.sql); la interfaz de
este módulo no debería cambiar.

Uso típico:

    repo = RepositorioReglas()

    regla_id = repo.crear_regla(
        empresa_id=1,
        nombre="Compras en Farmacias del Ahorro",
        tipo_movimiento="egreso",
        palabras_clave=["FARMACIAS DEL AHORRO", "FAHORRO"],
        plantilla=[
            {"cuenta": "6010-005", "naturaleza": "cargo", "formula": "BASE"},
            {"cuenta": "1180-001", "naturaleza": "cargo", "formula": "IVA"},
            {"cuenta": "1020-001", "naturaleza": "abono", "formula": "TOTAL"},
        ],
    )

    repo.desactivar_regla(regla_id)      # deja de aplicarse sin borrarla
    repo.eliminar_regla(regla_id)        # la borra por completo (con su plantilla)

    reglas, plantillas = repo.cargar_para_motor(empresa_id=1)
    # listas para pasarle directo a rule_engine.encontrar_regla(...)
"""

from typing import Optional

from db import get_connection
from rule_engine import Regla

class RepositorioReglas:
    """Las tablas que usa este repositorio (reglas, regla_palabras_clave,
    plantilla_movimientos) ya se crean en database/schema.sql al
    arrancar el servidor (ver db.inicializar_db()), así que este
    repositorio solo se conecta con db.get_connection(); ya no recibe
    ruta de archivo porque la base ahora es PostgreSQL, compartida por
    todas las instancias de la app."""

    def __init__(self, *args, **kwargs):
        pass

    def inicializar(self):
        """Compatibilidad con scripts legacy."""
        pass

    def _conectar(self):
        return get_connection()

    # -----------------------------------------------------------------
    # Crear / editar
    # -----------------------------------------------------------------

    def crear_regla(
        self,
        empresa_id: int,
        nombre: str,
        plantilla: list[dict],
        prioridad: int = 100,
        tipo_movimiento: Optional[str] = None,
        rfc_contraparte: Optional[str] = None,
        cuenta_bancaria_contraparte: Optional[str] = None,
        descripcion_exacta: Optional[str] = None,
        palabras_clave: Optional[list[str]] = None,
        creada_por: Optional[int] = None,
    ) -> int:
        """Crea una regla completa (condiciones + plantilla de movimientos)
        y regresa su id. `plantilla` es una lista de dicts:
        {"cuenta": ..., "naturaleza": "cargo"|"abono", "formula": ...,
         "descripcion_linea": ... (opcional)}"""
        with self._conectar() as con:
            cur = con.cursor()
            cur.execute(
                """INSERT INTO reglas
                   (empresa_id, nombre, prioridad, activa, rfc_contraparte,
                    cuenta_bancaria_contraparte, descripcion_exacta,
                    tipo_movimiento, creada_por)
                   VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?)""",
                (empresa_id, nombre, prioridad, rfc_contraparte,
                 cuenta_bancaria_contraparte, descripcion_exacta,
                 tipo_movimiento, creada_por),
            )
            regla_id = cur.lastrowid

            for palabra in (palabras_clave or []):
                cur.execute(
                    "INSERT INTO regla_palabras_clave (regla_id, palabra) VALUES (?, ?)",
                    (regla_id, palabra),
                )

            for orden, linea in enumerate(plantilla):
                cur.execute(
                    """INSERT INTO plantilla_movimientos
                       (regla_id, orden, cuenta, naturaleza, formula, descripcion_linea)
                       VALUES (?, ?, ?, ?, ?, ?)""",
                    (regla_id, orden, linea["cuenta"], linea["naturaleza"],
                     linea["formula"], linea.get("descripcion_linea")),
                )

            con.commit()
            return regla_id

    def agregar_palabra_clave(self, regla_id: int, palabra: str):
        with self._conectar() as con:
            con.execute(
                "INSERT INTO regla_palabras_clave (regla_id, palabra) VALUES (?, ?)",
                (regla_id, palabra),
            )
            con.commit()

    def quitar_palabra_clave(self, regla_id: int, palabra: str):
        with self._conectar() as con:
            con.execute(
                "DELETE FROM regla_palabras_clave WHERE regla_id = ? AND palabra = ?",
                (regla_id, palabra),
            )
            con.commit()

    def agregar_linea_plantilla(self, regla_id: int, cuenta: str, naturaleza: str,
                                 formula: str, descripcion_linea: Optional[str] = None,
                                 orden: Optional[int] = None):
        """Agrega un movimiento más a la póliza que genera esta regla
        (ej. "además, agrega un cargo del 4% a tal cuenta")."""
        with self._conectar() as con:
            cur = con.cursor()
            if orden is None:
                cur.execute(
                    "SELECT COALESCE(MAX(orden), -1) + 1 FROM plantilla_movimientos WHERE regla_id = ?",
                    (regla_id,),
                )
                orden = cur.fetchone()[0]
            cur.execute(
                """INSERT INTO plantilla_movimientos
                   (regla_id, orden, cuenta, naturaleza, formula, descripcion_linea)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (regla_id, orden, cuenta, naturaleza, formula, descripcion_linea),
            )
            con.commit()
            return cur.lastrowid

    def quitar_linea_plantilla(self, linea_id: int):
        with self._conectar() as con:
            con.execute("DELETE FROM plantilla_movimientos WHERE id = ?", (linea_id,))
            con.commit()

    def activar_regla(self, regla_id: int, empresa_id: Optional[int] = None) -> bool:
        return self._set_activa(regla_id, True, empresa_id=empresa_id)

    def desactivar_regla(self, regla_id: int, empresa_id: Optional[int] = None) -> bool:
        return self._set_activa(regla_id, False, empresa_id=empresa_id)

    def _set_activa(self, regla_id: int, activa: bool, empresa_id: Optional[int] = None) -> bool:
        with self._conectar() as con:
            if empresa_id is not None:
                cur = con.execute(
                    "UPDATE reglas SET activa = ? WHERE id = ? AND empresa_id = ?",
                    (1 if activa else 0, regla_id, empresa_id),
                )
            else:
                cur = con.execute(
                    "UPDATE reglas SET activa = ? WHERE id = ?",
                    (1 if activa else 0, regla_id),
                )
            con.commit()
            return cur.rowcount > 0

    def eliminar_regla(self, regla_id: int, empresa_id: Optional[int] = None) -> bool:
        """Borra la regla por completo, junto con sus palabras clave y
        su plantilla de movimientos (ON DELETE CASCADE). Si se especifica
        empresa_id, valida que la regla pertenezca a esa empresa."""
        with self._conectar() as con:
            if empresa_id is not None:
                cur = con.execute(
                    "DELETE FROM reglas WHERE id = ? AND empresa_id = ?",
                    (regla_id, empresa_id),
                )
            else:
                cur = con.execute("DELETE FROM reglas WHERE id = ?", (regla_id,))
            con.commit()
            return cur.rowcount > 0

    def registrar_uso(self, regla_id: int, con=None):
        """Se llama cada vez que una regla se aplica a un movimiento;
        sirve para saber cuáles reglas son las más usadas."""
        cerrar = con is None
        if cerrar:
            con = self._conectar()
        try:
            con.execute(
                "UPDATE reglas SET veces_aplicada = veces_aplicada + 1 WHERE id = ?",
                (regla_id,),
            )
            con.commit()
        finally:
            if cerrar:
                con.close()

    # -----------------------------------------------------------------
    # Consultar
    # -----------------------------------------------------------------

    def listar_reglas(self, empresa_id: int, solo_activas: bool = False) -> list[dict]:
        with self._conectar() as con:
            query = "SELECT * FROM reglas WHERE empresa_id = ?"
            params = [empresa_id]
            if solo_activas:
                query += " AND activa = 1"
            query += " ORDER BY prioridad ASC"
            filas = con.execute(query, params).fetchall()
            return [dict(f) for f in filas]

    def listar_reglas_completas(self, empresa_id: int) -> list[dict]:
        """Trae todas las reglas de una empresa con sus palabras clave y plantillas
        en exactamente 3 consultas batch, eliminando el problema N+1."""
        with self._conectar() as con:
            filas_reglas = con.execute(
                "SELECT * FROM reglas WHERE empresa_id = ? ORDER BY prioridad ASC",
                (empresa_id,),
            ).fetchall()
            reglas = [dict(f) for f in filas_reglas]
            if not reglas:
                return []

            r_ids_str = ",".join(str(int(r["id"])) for r in reglas)

            filas_palabras = con.execute(
                f"SELECT regla_id, palabra FROM regla_palabras_clave WHERE regla_id IN ({r_ids_str})"
            ).fetchall()
            palabras_por_regla: dict[int, list[str]] = {}
            for fp in filas_palabras:
                palabras_por_regla.setdefault(fp["regla_id"], []).append(fp["palabra"])

            filas_lineas = con.execute(
                f"SELECT * FROM plantilla_movimientos WHERE regla_id IN ({r_ids_str}) ORDER BY orden ASC"
            ).fetchall()

            # Enriquecer con nombres de cuenta del catálogo
            filas_cuentas = con.execute(
                "SELECT cuenta, descripcion FROM cuentas_catalogo WHERE empresa_id = ?",
                (empresa_id,),
            ).fetchall()
            nombres_cuentas = {}
            for fc in filas_cuentas:
                c_orig = str(fc["cuenta"] or "").strip()
                c_clean = c_orig.replace("-", "").replace(".", "")
                nombres_cuentas[c_orig] = fc["descripcion"]
                nombres_cuentas[c_clean] = fc["descripcion"]

            plantillas_por_regla: dict[int, list[dict]] = {}
            for fl in filas_lineas:
                linea_d = dict(fl)
                cta = str(linea_d.get("cuenta") or "").strip()
                cta_clean = cta.replace("-", "").replace(".", "")
                linea_d["nombre_cuenta"] = nombres_cuentas.get(cta) or nombres_cuentas.get(cta_clean) or ""
                plantillas_por_regla.setdefault(fl["regla_id"], []).append(linea_d)

            for r in reglas:
                r["palabras_clave"] = palabras_por_regla.get(r["id"], [])
                r["plantilla"] = plantillas_por_regla.get(r["id"], [])

            return reglas

    def obtener_plantilla(self, regla_id: int) -> list[dict]:
        with self._conectar() as con:
            filas = con.execute(
                "SELECT * FROM plantilla_movimientos WHERE regla_id = ? ORDER BY orden ASC",
                (regla_id,),
            ).fetchall()
            return [dict(f) for f in filas]

    def obtener_palabras_clave(self, regla_id: int) -> list[str]:
        with self._conectar() as con:
            filas = con.execute(
                "SELECT palabra FROM regla_palabras_clave WHERE regla_id = ?",
                (regla_id,),
            ).fetchall()
            return [f[0] for f in filas]

    def cargar_para_motor(self, empresa_id: int):
        """
        Trae todas las reglas activas de una empresa ya armadas como
        objetos `Regla` (de rule_engine.py), listas para pasarle a
        `encontrar_regla(movimiento, reglas)`, junto con un diccionario
        {regla_id: [LineaPlantilla, ...]} para generar_poliza().
        Usa exactamente 3 consultas batch sin N+1.
        """
        from policy_generator import LineaPlantilla

        with self._conectar() as con:
            filas_reglas = con.execute(
                "SELECT * FROM reglas WHERE empresa_id = ? AND activa = 1 ORDER BY prioridad ASC",
                (empresa_id,),
            ).fetchall()
            reglas_raw = [dict(f) for f in filas_reglas]
            if not reglas_raw:
                return [], {}

            r_ids_str = ",".join(str(int(r["id"])) for r in reglas_raw)

            filas_palabras = con.execute(
                f"SELECT regla_id, palabra FROM regla_palabras_clave WHERE regla_id IN ({r_ids_str})"
            ).fetchall()
            palabras_por_regla: dict[int, list[str]] = {}
            for fp in filas_palabras:
                palabras_por_regla.setdefault(fp["regla_id"], []).append(fp["palabra"])

            filas_lineas = con.execute(
                f"SELECT * FROM plantilla_movimientos WHERE regla_id IN ({r_ids_str}) ORDER BY orden ASC"
            ).fetchall()
            lineas_por_regla: dict[int, list[LineaPlantilla]] = {}
            for fl in filas_lineas:
                lineas_por_regla.setdefault(fl["regla_id"], []).append(
                    LineaPlantilla(
                        cuenta=fl["cuenta"],
                        naturaleza=fl["naturaleza"],
                        formula=fl["formula"],
                        descripcion_linea=fl["descripcion_linea"],
                    )
                )

            reglas = []
            plantillas = {}
            for r in reglas_raw:
                palabras = palabras_por_regla.get(r["id"], [])
                reglas.append(Regla(
                    id=r["id"],
                    empresa_id=r["empresa_id"],
                    nombre=r["nombre"],
                    prioridad=r["prioridad"],
                    activa=bool(r["activa"]),
                    rfc_contraparte=r["rfc_contraparte"],
                    cuenta_bancaria_contraparte=r["cuenta_bancaria_contraparte"],
                    descripcion_exacta=r["descripcion_exacta"],
                    descripcion_contiene=palabras,
                    tipo_movimiento=r["tipo_movimiento"],
                ))
                plantillas[r["id"]] = lineas_por_regla.get(r["id"], [])

            return reglas, plantillas

