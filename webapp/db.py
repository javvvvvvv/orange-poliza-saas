# ============================================================================
#    PROPIEDAD INTELECTUAL Y LICENCIA COMERCIAL CERRADA
#    ============================================================================
#    Autor Legal y Titular de Derechos: JAVIER ILLAN GONZALEZ
#    Organización: ORANGE CREW
#    Contacto: ILLANJAVIER9@GMAIL.COM
#
#    ADVERTENCIA LEGAL (MÉXICO Y GLOBAL):
#    Este código fuente y su arquitectura son propiedad intelectual exclusiva de
#    JAVIER ILLAN GONZALEZ. Queda estrictamente prohibida su reproducción,
#    distribución, modificación, ingeniería inversa, copia o uso comercial sin la
#    autorización expresa y por escrito del autor. Obra protegida conforme a la
#    Ley Federal del Derecho de Autor y tratados internacionales aplicables.
#    ============================================================================
"""
Capa de acceso a datos de Orange Poliza Engine sobre PostgreSQL.

Expone una capa de compatibilidad y manejo seguro del pool:
  - con.execute("... WHERE id = ?", (valor,))   -> placeholders '?'
  - cur.lastrowid                                -> vía RETURNING id
  - fila["columna"] y fila[0]                    -> igual que sqlite3.Row
  - with get_connection() as con:                -> context manager que hace
                                                    commit/rollback y devuelve
                                                    la conexión al pool siempre
"""

from contextlib import contextmanager
from decimal import Decimal
import os
import re
import sys

import psycopg2
import psycopg2.extensions
from psycopg2.pool import ThreadedConnectionPool
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))

CORE_DIR = os.path.abspath(os.path.join(BASE_DIR, "..", "backend", "app", "core"))
if CORE_DIR not in sys.path:
    sys.path.insert(0, CORE_DIR)

try:
    from formula_engine import MontoDecimal
    psycopg2.extensions.register_adapter(MontoDecimal, psycopg2.extensions.Decimal)
except Exception:
    MontoDecimal = Decimal

SCHEMA_PATH = os.path.join(BASE_DIR, "..", "database", "schema.sql")

# Tablas cuya llave primaria no se llama "id" (o no tienen una sola
# columna autoincremental): no se les debe agregar "RETURNING id".
_TABLAS_SIN_ID = {"usuario_empresa"}

_INSERT_RE = re.compile(r"^\s*INSERT\s+INTO\s+([a-zA-Z_][a-zA-Z0-9_]*)", re.IGNORECASE)

class ErrorConexionBD(Exception):
    """Se lanza cuando falta configuración de conexión a PostgreSQL o el
    servidor no responde, con un mensaje entendible para el usuario final."""
    pass

def _config_desde_entorno() -> dict:
    requeridas = ("ORANGE_DB_HOST", "ORANGE_DB_NAME", "ORANGE_DB_USER", "ORANGE_DB_PASSWORD")
    faltantes = [v for v in requeridas if not os.environ.get(v)]
    if faltantes:
        raise ErrorConexionBD(
            "Falta configurar la conexión a PostgreSQL (variables de entorno: "
            + ", ".join(faltantes) + "). Copia webapp/.env.example a webapp/.env "
            "con los datos de tu servidor, o corre instalar_postgres.bat en la "
            "computadora que actuará como servidor."
        )
    return {
        "host": os.environ["ORANGE_DB_HOST"],
        "port": os.environ.get("ORANGE_DB_PORT", "5432"),
        "dbname": os.environ["ORANGE_DB_NAME"],
        "user": os.environ["ORANGE_DB_USER"],
        "password": os.environ["ORANGE_DB_PASSWORD"],
        "connect_timeout": 5,
    }

def _normalizar_valor(v):
    if isinstance(v, Decimal) and not isinstance(v, MontoDecimal):
        return MontoDecimal(v)
    return v

class Row:
    """Emula sqlite3.Row: accesible por nombre (fila["columna"]) o por
    posición (fila[0]), e iterable/convertible con dict(fila)."""

    __slots__ = ("_valores", "_columnas")

    def __init__(self, columnas, valores):
        self._columnas = columnas
        self._valores = {col: _normalizar_valor(val) for col, val in zip(columnas, valores)}

    def __getitem__(self, clave):
        if isinstance(clave, int):
            return self._valores[self._columnas[clave]]
        return self._valores[clave]

    def get(self, clave, default=None):
        return self._valores.get(clave, default)

    def keys(self):
        return list(self._columnas)

    def __contains__(self, clave):
        return clave in self._valores

    def __iter__(self):
        return iter(self._columnas)

    def __eq__(self, otro):
        return isinstance(otro, Row) and self._valores == otro._valores

    def __repr__(self):
        return f"Row({self._valores!r})"

def _traducir_placeholders(sql: str) -> str:
    """Los repositorios están escritos con '?' al estilo sqlite3;
    psycopg2 espera '%s'."""
    return sql.replace("?", "%s")

class Cursor:
    def __init__(self, cursor_real):
        self._cursor = cursor_real
        self.lastrowid = None

    def execute(self, sql, params=()):
        sql_pg = _traducir_placeholders(sql)
        agregar_returning = False
        if "RETURNING" not in sql_pg.upper():
            m = _INSERT_RE.match(sql_pg)
            if m and m.group(1).lower() not in _TABLAS_SIN_ID:
                sql_pg = sql_pg.rstrip().rstrip(";") + " RETURNING id"
                agregar_returning = True
        self._cursor.execute(sql_pg, params)
        if agregar_returning:
            fila = self._cursor.fetchone()
            self.lastrowid = fila[0] if fila else None
        return self

    def executemany(self, sql, secuencia_params):
        self._cursor.executemany(_traducir_placeholders(sql), secuencia_params)
        return self

    def _empaquetar(self, fila):
        if fila is None:
            return None
        columnas = [d[0] for d in self._cursor.description]
        return Row(columnas, fila)

    def fetchone(self):
        return self._empaquetar(self._cursor.fetchone())

    def fetchall(self):
        return [self._empaquetar(f) for f in self._cursor.fetchall()]

    @property
    def rowcount(self):
        return self._cursor.rowcount

    def close(self):
        self._cursor.close()

class Connection:
    def __init__(self, con_real):
        self._con = con_real
        self._cerrada = False
        self.row_factory = None  # solo por compatibilidad; Row siempre está activo

    def __enter__(self) -> "Connection":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        try:
            if exc_type is not None:
                self.rollback()
            else:
                self.commit()
        finally:
            self.close()
        return False

    def cursor(self) -> Cursor:
        return Cursor(self._con.cursor())

    def execute(self, sql, params=()) -> Cursor:
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def executescript(self, sql: str) -> None:
        """Ejecuta un bloque con varias sentencias separadas por ';'
        (usado para correr database/schema.sql completo)."""
        cur = self._con.cursor()
        try:
            cur.execute(sql)
        finally:
            cur.close()

    def commit(self):
        if not self._cerrada:
            self._con.commit()

    def rollback(self):
        if not self._cerrada:
            try:
                self._con.rollback()
            except Exception:
                pass

    def close(self):
        if self._cerrada:
            return
        self._cerrada = True
        if _pool is not None:
            try:
                _pool.putconn(self._con)
            except Exception:
                pass
        else:
            try:
                self._con.close()
            except Exception:
                pass

    def __del__(self):
        # Salvaguarda final: si una referencia sale de alcance sin haberse
        # cerrado explícitamente, la devuelve al pool para evitar fugas.
        try:
            self.close()
        except Exception:
            pass

def _texto_legible(excepcion: Exception) -> str:
    crudo = getattr(excepcion, "object", None)
    if isinstance(crudo, (bytes, bytearray)):
        for codificacion in ("utf-8", "cp1252", "latin-1"):
            try:
                return crudo.decode(codificacion)
            except UnicodeDecodeError:
                continue
        return repr(bytes(crudo))
    return str(excepcion) or repr(excepcion)

_pool = None

def get_connection() -> Connection:
    global _pool
    if _pool is None:
        try:
            _pool = ThreadedConnectionPool(1, 20, **_config_desde_entorno())
        except (psycopg2.OperationalError, UnicodeDecodeError, UnicodeError) as e:
            raise ErrorConexionBD(
                "No se pudo conectar al servidor PostgreSQL. Verifica que el "
                "servicio de PostgreSQL esté iniciado (en la computadora servidor, "
                'abre "Servicios" de Windows y busca "postgresql-x64-..."), que el '
                "puerto 5432 esté abierto en el Firewall, y que webapp/.env tenga "
                f"los datos correctos. Detalle: {_texto_legible(e)}"
            ) from e

    con = _pool.getconn()
    return Connection(con)

@contextmanager
def connection_scope():
    """Context manager explícito para operaciones de base de datos."""
    con = get_connection()
    try:
        yield con
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()

def inicializar_db():
    """Crea las tablas que falten (CREATE TABLE IF NOT EXISTS) y agrega
    columnas nuevas / migra tipos en instalaciones existentes."""
    with get_connection() as con:
        with open(SCHEMA_PATH, encoding="utf-8") as f:
            con.executescript(f.read())
        con.commit()
        _aplicar_migraciones(con)

def _aplicar_migraciones(con: Connection):
    """Columnas agregadas y migración de REAL a NUMERIC para precisión decimal."""
    migraciones = [
        ("organizaciones", "limite_usuarios", "INTEGER NOT NULL DEFAULT 5"),
        ("organizaciones", "estado_suscripcion", "TEXT NOT NULL DEFAULT 'activa'"),
        ("organizaciones", "fecha_vencimiento", "TEXT"),
        ("empresas", "tasa_retencion_iva", "NUMERIC(9,6) NOT NULL DEFAULT 0.0"),
        ("empresas", "tasa_retencion_isr", "NUMERIC(9,6) NOT NULL DEFAULT 0.0"),
        ("empresas", "base_datos_contpaqi", "TEXT"),
        ("documentos_importados", "ruta_archivo", "TEXT"),
        ("documentos_importados", "nombre_hoja", "TEXT"),
        ("movimientos", "fila_original", "INTEGER"),
        ("movimientos", "afectable_impuestos", "INTEGER"),
        ("cfdi_impuestos", "es_retencion", "INTEGER NOT NULL DEFAULT 0"),
        ("cfdi_impuestos", "tipo_impuesto", "TEXT NOT NULL DEFAULT 'IVA'"),
        ("empresas", "sql_servidor", "TEXT"),
        ("empresas", "sql_usuario", "TEXT"),
        ("empresas", "sql_password", "TEXT"),
        ("empresas", "sql_base_datos", "TEXT"),
        ("polizas", "concepto", "TEXT"),
        ("cfdis", "nombre_emisor", "TEXT"),
        ("cfdis", "nombre_receptor", "TEXT"),
        ("cfdis", "uuids_relacionados", "TEXT"),
        ("documentos_importados", "hash_sha256", "TEXT"),
        ("polizas", "enviada_contpaqi", "INTEGER NOT NULL DEFAULT 0"),
        ("polizas", "fecha_envio", "TEXT"),
        ("cfdis", "tipo_comprobante", "TEXT NOT NULL DEFAULT 'I'"),
        ("cfdis", "moneda", "TEXT NOT NULL DEFAULT 'MXN'"),
        ("cfdis", "tipo_cambio", "NUMERIC(18,6) NOT NULL DEFAULT 1.0"),
        ("cfdis", "metodo_pago", "TEXT"),
        ("cfdis", "descuento", "NUMERIC(18,2) NOT NULL DEFAULT 0.0"),
        ("cfdi_impuestos", "tipo_factor", "TEXT NOT NULL DEFAULT 'Tasa'"),
        ("empresas", "cuenta_retencion_iva", "TEXT"),
        ("empresas", "cuenta_retencion_isr", "TEXT"),
        ("cuentas_catalogo", "id_contpaqi", "INTEGER"),
        ("cuentas_catalogo", "id_comparacion", "TEXT"),
    ]
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS auditoria_despacho (
        id SERIAL PRIMARY KEY,
        organizacion_id INTEGER NOT NULL REFERENCES organizaciones(id) ON DELETE CASCADE,
        usuario_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
        accion TEXT NOT NULL,
        detalle TEXT,
        fecha TEXT NOT NULL DEFAULT (NOW()::text)
    );

    CREATE TABLE IF NOT EXISTS respaldos_cargas (
        id SERIAL PRIMARY KEY,
        empresa_id INTEGER NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
        usuario_id INTEGER REFERENCES usuarios(id) ON DELETE SET NULL,
        fecha TEXT NOT NULL DEFAULT (NOW()::text),
        motivo TEXT,
        total_movimientos INTEGER DEFAULT 0,
        total_polizas INTEGER DEFAULT 0,
        archivo_respaldo TEXT,
        datos_json TEXT
    );
    """)

    for tabla, columna, tipo in migraciones:
        cur.execute(f"ALTER TABLE {tabla} ADD COLUMN IF NOT EXISTS {columna} {tipo}")

    # Migración idempotente de columnas REAL (float4) a NUMERIC exacto (C1)
    conversiones_numeric = [
        ("empresas", "tasa_iva", "NUMERIC(9,6)", 6),
        ("empresas", "tasa_retencion_iva", "NUMERIC(9,6)", 6),
        ("empresas", "tasa_retencion_isr", "NUMERIC(9,6)", 6),
        ("poliza_lineas", "importe", "NUMERIC(18,2)", 2),
        ("movimientos", "total", "NUMERIC(18,2)", 2),
        ("movimientos", "tipo_cambio", "NUMERIC(18,6)", 6),
        ("cfdis", "total", "NUMERIC(18,2)", 2),
        ("cfdis", "subtotal", "NUMERIC(18,2)", 2),
        ("cfdi_impuestos", "base", "NUMERIC(18,2)", 2),
        ("cfdi_impuestos", "importe", "NUMERIC(18,2)", 2),
        ("cfdi_impuestos", "tasa", "NUMERIC(9,6)", 6),
        ("cfdi_movimiento", "importe_aplicado", "NUMERIC(18,2)", 2),
    ]
    for tabla, columna, tipo_sql, escala in conversiones_numeric:
        cur.execute(
            """SELECT data_type FROM information_schema.columns
               WHERE table_schema = 'public' AND table_name = ? AND column_name = ?""",
            (tabla, columna),
        )
        info = cur.fetchone()
        if info and info[0].lower() != "numeric":
            cur.execute(
                f"ALTER TABLE {tabla} ALTER COLUMN {columna} TYPE {tipo_sql} "
                f"USING ROUND({columna}::numeric, {escala})"
            )

    # Migrar todas las llaves foráneas de cascada del esquema a ON DELETE CASCADE
    fks_cascade = [
        ("usuarios", "organizacion_id", "organizaciones", "id"),
        ("empresas", "organizacion_id", "organizaciones", "id"),
        ("usuario_empresa", "usuario_id", "usuarios", "id"),
        ("usuario_empresa", "empresa_id", "empresas", "id"),
        ("cuentas_catalogo", "empresa_id", "empresas", "id"),
        ("bancos", "empresa_id", "empresas", "id"),
        ("documentos_importados", "empresa_id", "empresas", "id"),
        ("reglas", "empresa_id", "empresas", "id"),
        ("regla_palabras_clave", "regla_id", "reglas", "id"),
        ("plantilla_movimientos", "regla_id", "reglas", "id"),
        ("reglas_generales", "empresa_id", "empresas", "id"),
        ("polizas", "empresa_id", "empresas", "id"),
        ("poliza_lineas", "poliza_id", "polizas", "id"),
        ("poliza_auditoria", "poliza_id", "polizas", "id"),
        ("movimientos", "empresa_id", "empresas", "id"),
        ("cfdis", "empresa_id", "empresas", "id"),
        ("cfdi_impuestos", "cfdi_id", "cfdis", "id"),
        ("cfdi_movimiento", "cfdi_id", "cfdis", "id"),
        ("cfdi_movimiento", "movimiento_id", "movimientos", "id"),
        ("memoria_ml", "empresa_id", "empresas", "id"),
        ("memoria_ml", "regla_asignada_id", "reglas", "id"),
        ("auditoria_despacho", "organizacion_id", "organizaciones", "id"),
    ]
    for tabla_fk, col_fk, tabla_ref, col_ref in fks_cascade:
        cur.execute(
            """SELECT c.conname
               FROM pg_constraint c
               JOIN pg_class t ON c.conrelid = t.oid
               JOIN pg_namespace n ON t.relnamespace = n.oid
               JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(c.conkey)
               WHERE n.nspname = 'public'
                 AND t.relname = ?
                 AND a.attname = ?
                 AND c.contype = 'f'
                 AND c.confdeltype <> 'c'""",
            (tabla_fk, col_fk),
        )
        for fk_row in cur.fetchall():
            conname = fk_row[0]
            cur.execute(f"ALTER TABLE {tabla_fk} DROP CONSTRAINT {conname}")
            cur.execute(
                f"ALTER TABLE {tabla_fk} ADD CONSTRAINT {conname} "
                f"FOREIGN KEY ({col_fk}) REFERENCES {tabla_ref}({col_ref}) ON DELETE CASCADE"
            )

    cur.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_usuarios_usuario ON usuarios(usuario)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_movimientos_empresa_estado ON movimientos(empresa_id, estado_clasificacion)"
    )
    cur.execute(
        "CREATE INDEX IF NOT EXISTS idx_movimientos_poliza_id ON movimientos(poliza_id)"
    )

def hay_usuarios() -> bool:
    with get_connection() as con:
        fila = con.execute("SELECT COUNT(*) FROM usuarios").fetchone()
        return fila[0] > 0
