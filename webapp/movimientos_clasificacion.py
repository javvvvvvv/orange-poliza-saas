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

from collections import Counter

import _rutas  # noqa: F401  (deja backend/app/core y exporters en sys.path)

from ml_engine import EntrenadorML
from rules_repository import RepositorioReglas
from cfdi_asociados import aplicar_impuestos_cfdi, cargar_cfdis_por_movimiento, reconciliar_rfc
from rule_engine import encontrar_regla
from db import get_connection
from learning_engine import sugerir_regla_desde_clasificacion_manual
from movimientos_comun import (
    _SQL_MOVIMIENTOS_CON_BANCO,
    _cargar_config_fiscal,
    _fila_a_movimiento_dict,
    _generar_para_movimiento,
    _info_descuadre,
    _info_incidencia,
)


def registrar_memoria_ml_exitosa(empresa_id, descripcion, tipo, regla_id, con=None):
    sql = """INSERT INTO memoria_ml (empresa_id, descripcion_limpia, tipo, regla_asignada_id)
             VALUES (?, ?, ?, ?)
             ON CONFLICT(empresa_id, descripcion_limpia, tipo, regla_asignada_id)
             DO UPDATE SET frecuencia = memoria_ml.frecuencia + 1, ultima_vez = CURRENT_TIMESTAMP"""
    params = (empresa_id, str(descripcion).strip(), str(tipo).strip(), regla_id)
    if con is not None:
        con.execute(sql, params)
        return
    with get_connection() as con_propia:
        con_propia.execute(sql, params)


def entrenar_modelo_empresa(empresa_id):
    with get_connection() as con:
        datos = con.execute(
            "SELECT descripcion_limpia as descripcion, tipo, regla_asignada_id as regla_id FROM memoria_ml WHERE empresa_id = ?",
            (empresa_id,),
        ).fetchall()
    if datos:
        EntrenadorML.entrenar(empresa_id, [dict(d) for d in datos])


def clasificar_pendientes(empresa_id):
    """Corre el motor de reglas sobre los movimientos pendientes.

    Antes de buscar regla se toma el RFC del CFDI confirmado, y si contradice el
    del movimiento el movimiento no se clasifica solo.
    """
    repo = RepositorioReglas()
    reglas, plantillas = repo.cargar_para_motor(empresa_id)

    automaticos = 0
    usos_por_regla = Counter()
    incidencias = []

    with get_connection() as con:
        cfg = _cargar_config_fiscal(con, empresa_id)
        pendientes = con.execute(
            _SQL_MOVIMIENTOS_CON_BANCO
            + " WHERE m.empresa_id = ? AND m.estado_clasificacion = 'pendiente' ORDER BY m.fecha, m.id",
            (empresa_id,),
        ).fetchall()
        cfdis_por_movimiento = cargar_cfdis_por_movimiento(con, [f["id"] for f in pendientes])

        for fila in pendientes:
            mov = _fila_a_movimiento_dict(fila)
            cfdis = cfdis_por_movimiento.get(fila["id"], [])

            conflicto = reconciliar_rfc(mov, cfdis)
            if conflicto:
                incidencias.append(_info_incidencia(mov, "", conflicto, "conflicto_rfc"))
                continue

            match = encontrar_regla(mov, reglas)
            if match.regla is None:
                continue

            plantilla = plantillas.get(match.regla.id)
            if not plantilla:
                incidencias.append(_info_incidencia(
                    mov, match.regla.nombre, "la regla no tiene plantilla contable", "sin_plantilla"))
                continue

            aplicar_impuestos_cfdi(mov, cfdis)
            resultado = _generar_para_movimiento(mov, plantilla, match.regla.nombre, match.motivo, cfg)
            if not resultado.cuadrada:
                incidencias.append(_info_descuadre(mov, match.regla.nombre, resultado))
                continue

            con.execute(
                """UPDATE movimientos
                   SET regla_id = ?, confianza_clasificacion = ?, estado_clasificacion = 'automatico'
                   WHERE id = ? AND empresa_id = ?""",
                (match.regla.id, match.confianza, fila["id"], empresa_id),
            )
            usos_por_regla[match.regla.id] += 1
            automaticos += 1
            registrar_memoria_ml_exitosa(empresa_id, mov["descripcion"], mov["tipo"], match.regla.id, con=con)

        for regla_id, veces in usos_por_regla.items():
            con.execute(
                "UPDATE reglas SET veces_aplicada = veces_aplicada + ? WHERE id = ?",
                (veces, regla_id),
            )

    return {
        "automaticos": automaticos,
        "pendientes_restantes": len(pendientes) - automaticos,
        "descuadres": incidencias,
    }


def listar_movimientos(empresa_id, estado=None):
    with get_connection() as con:
        query = "SELECT * FROM movimientos WHERE empresa_id = ?"
        params = [empresa_id]
        if estado:
            query += " AND estado_clasificacion = ?"
            params.append(estado)
        query += " ORDER BY fecha DESC, id DESC"
        filas = con.execute(query, params).fetchall()
    return filas


def sugerencia_para_movimiento(movimiento_id, empresa_id=None):
    with get_connection() as con:
        if empresa_id is not None:
            fila = con.execute(
                "SELECT * FROM movimientos WHERE id = ? AND empresa_id = ?",
                (movimiento_id, empresa_id),
            ).fetchone()
        else:
            fila = con.execute("SELECT * FROM movimientos WHERE id = ?", (movimiento_id,)).fetchone()
        if not fila:
            return None, None
        empresa = con.execute("SELECT rfc FROM empresas WHERE id = ?", (fila["empresa_id"],)).fetchone()

    mov = _fila_a_movimiento_dict(fila)
    rfc_empresa = empresa["rfc"] if empresa else ""
    return mov, sugerir_regla_desde_clasificacion_manual(mov, rfc_empresa=rfc_empresa)
