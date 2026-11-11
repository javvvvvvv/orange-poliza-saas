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

from decimal import Decimal
from dataclasses import dataclass
from datetime import datetime

import _rutas  # noqa: F401  (deja backend/app/core y exporters en sys.path)

from contpaqi_exporter import FacturaAplicada, ImpuestoPoliza, MovimientoPoliza
from formula_engine import MontoDecimal, a_decimal, redondear_moneda
from cfdi_asociados import cargar_cfdis_por_movimiento, rfc_contraparte_cfdi
from contpaqi_txt_exporter import exportar_polizas_contpaqi_txt
from db import get_connection
from licencia import verificar_licencia_empresa
from movimientos_comun import (
    EmpresaNoEncontradaError,
    _SQL_MOVIMIENTOS_CON_BANCO,
    _lista_ids,
    _sin_guiones,
)


class SinPolizasPorExportarError(Exception):
    pass


class BancoSinCuentaContableError(Exception):
    def __init__(self, polizas):
        self.polizas = list(polizas)
        super().__init__(
            "Estas pólizas vienen de un banco sin cuenta contable configurada: "
            + ", ".join(self.polizas)
        )


@dataclass(frozen=True)
class _LineaExportable:
    cuenta: str
    naturaleza: str
    importe: Decimal
    descripcion: str


def _cargar_lineas(con, poliza_ids) -> dict:
    por_poliza: dict = {}
    filas = con.execute(
        f"SELECT * FROM poliza_lineas WHERE poliza_id IN ({_lista_ids(poliza_ids)}) ORDER BY poliza_id, orden"
    ).fetchall()
    for fila in filas:
        por_poliza.setdefault(fila["poliza_id"], []).append(_LineaExportable(
            cuenta=fila["cuenta"],
            naturaleza=fila["naturaleza"],
            importe=redondear_moneda(fila["importe"]),
            descripcion=fila["descripcion"],
        ))
    return por_poliza


def _cargar_movimientos_de_polizas(con, poliza_ids) -> dict:
    filas = con.execute(
        _SQL_MOVIMIENTOS_CON_BANCO + f" WHERE m.poliza_id IN ({_lista_ids(poliza_ids)})"
    ).fetchall()
    return {f["poliza_id"]: f for f in filas}


def _cargar_impuestos_cfdi(con, cfdi_ids) -> dict:
    por_cfdi: dict = {}
    if not cfdi_ids:
        return por_cfdi
    for fila in con.execute(
        f"SELECT * FROM cfdi_impuestos WHERE cfdi_id IN ({_lista_ids(cfdi_ids)})"
    ).fetchall():
        por_cfdi.setdefault(fila["cfdi_id"], []).append(fila)
    return por_cfdi


def _validar_cuentas_banco(polizas, movimientos_por_poliza):
    sin_cuenta = [
        f"{p['tipo']} {p['numero']}"
        for p in polizas
        if p["id"] in movimientos_por_poliza
        and not _sin_guiones(movimientos_por_poliza[p["id"]]["cuenta_banco"])
    ]
    if sin_cuenta:
        raise BancoSinCuentaContableError(sin_cuenta)


def _facturas_desde_cfdis(cfdis, impuestos_por_cfdi):
    facturas = []
    impuestos_totales = []
    for cfdi in cfdis:
        uuid_final = cfdi["uuid"]
        relacionados = (cfdi["uuids_relacionados"] or "").split(",")
        if relacionados[0].strip():
            uuid_final = relacionados[0].strip()

        total_cfdi = a_decimal(cfdi["cfdi_total"])
        factor = (
            min(Decimal("1"), a_decimal(cfdi["importe_aplicado"]) / total_cfdi)
            if total_cfdi else MontoDecimal("1.0")
        )
        impuestos = [
            ImpuestoPoliza(
                base=redondear_moneda(a_decimal(i["base"]) * factor),
                importe=redondear_moneda(a_decimal(i["importe"]) * factor),
                tasa=a_decimal(i["tasa"]),
                es_retencion=bool(i["es_retencion"]),
                tipo_impuesto=i["tipo_impuesto"] or "IVA",
            )
            for i in impuestos_por_cfdi.get(cfdi["cfdi_id"], [])
        ]
        facturas.append(FacturaAplicada(
            uuid=uuid_final,
            serie=cfdi["serie"] or "",
            folio=cfdi["folio"] or "",
            rfc_persona=rfc_contraparte_cfdi(cfdi),
            impuestos=impuestos,
        ))
        impuestos_totales.extend(impuestos)
    return facturas, impuestos_totales


def _cuentas_iva(empresa, tipo_poliza) -> set:
    """Cuentas de IVA (definitivas y transitorias) que corresponden al tipo de póliza."""
    claves = (
        ("cuenta_iva_acreditable", "cuenta_iva_por_acreditar") if tipo_poliza == "egreso"
        else ("cuenta_iva_trasladado", "cuenta_iva_por_trasladar")
    )
    return {_sin_guiones(empresa.get(k)) for k in claves} - {""}


def _impuesto_estimado(poliza, lineas, movimiento, empresa) -> ImpuestoPoliza:
    """Impuesto de una póliza sin CFDI, leído de la línea de IVA que ya tiene la póliza."""
    total = redondear_moneda(movimiento["total"])
    cuentas_iva = _cuentas_iva(empresa, poliza["tipo"])
    linea_iva = next((l for l in lineas if _sin_guiones(l.cuenta) in cuentas_iva), None)
    if linea_iva is None:
        return ImpuestoPoliza(base=total, importe=MontoDecimal("0.00"), tasa=MontoDecimal("0.0"))
    importe = redondear_moneda(linea_iva.importe)
    return ImpuestoPoliza(
        base=redondear_moneda(total - importe),
        importe=importe,
        tasa=a_decimal(empresa["tasa_iva"], "0.16"),
    )


def _armar_movimiento_poliza(poliza, lineas, movimiento, cfdis, impuestos_por_cfdi, empresa):
    cfdi_uuid = cfdi_serie = cfdi_folio = None
    cfdi_impuestos = []
    facturas = []
    cuenta_banco = None

    if cfdis:
        cuenta_banco = _sin_guiones(movimiento["cuenta_banco"])
        facturas, cfdi_impuestos = _facturas_desde_cfdis(cfdis, impuestos_por_cfdi)
        if len(facturas) == 1:
            cfdi_uuid, cfdi_serie, cfdi_folio = facturas[0].uuid, facturas[0].serie, facturas[0].folio
    elif movimiento is not None and (
        movimiento["afectable_impuestos"] is None or movimiento["afectable_impuestos"]
    ):
        cuenta_banco = _sin_guiones(movimiento["cuenta_banco"])
        cfdi_impuestos = [_impuesto_estimado(poliza, lineas, movimiento, empresa)]

    return MovimientoPoliza(
        numero_poliza=poliza["numero"],
        tipo=poliza["tipo"],
        fecha=datetime.strptime(poliza["fecha"], "%Y-%m-%d"),
        descripcion=poliza.get("concepto") or (movimiento["descripcion"] if movimiento else ""),
        lineas=lineas,
        tiene_iva=bool(cfdi_uuid or facturas),
        numero_factura=poliza["referencia"] or "",
        cfdi_uuid=cfdi_uuid,
        cfdi_serie=cfdi_serie,
        cfdi_folio=cfdi_folio,
        cfdi_impuestos=cfdi_impuestos,
        facturas=facturas,
        cuenta_banco=cuenta_banco,
    )


def exportar_polizas(empresa_id, ruta_salida, reexportar=False, marcar_enviadas=False):
    """Exporta a archivo de texto / Excel para CONTPAQi solo pólizas cuadradas.

    Descargar o exportar el archivo NO marca las pólizas como enviadas a CONTPAQi,
    para evitar enviar o bloquear pólizas sin autorización explícita del usuario.
    Solo el envío directo vía SDK o cuando se pida explícitamente `marcar_enviadas=True`
    marcará el registro.
    """
    with get_connection() as con:
        empresa = con.execute("SELECT * FROM empresas WHERE id = ?", (empresa_id,)).fetchone()
        if empresa is None:
            raise EmpresaNoEncontradaError(f"La empresa {empresa_id} no existe.")
        verificar_licencia_empresa(empresa["nombre"])

        filtro_envio = "" if reexportar else " AND enviada_contpaqi = 0"
        polizas = con.execute(
            f"SELECT * FROM polizas WHERE empresa_id = ? AND cuadrada = 1{filtro_envio} ORDER BY tipo, numero",
            (empresa_id,),
        ).fetchall()
        if not polizas:
            descuadradas = con.execute(
                f"SELECT COUNT(*) AS n FROM polizas WHERE empresa_id = ? AND cuadrada = 0{filtro_envio}",
                (empresa_id,),
            ).fetchone()["n"]
            detalle = f" Hay {descuadradas} póliza(s) descuadrada(s) por corregir." if descuadradas else ""
            raise SinPolizasPorExportarError("No hay pólizas cuadradas pendientes de exportar." + detalle)

        poliza_ids = [p["id"] for p in polizas]
        lineas_por_poliza = _cargar_lineas(con, poliza_ids)
        movimientos_por_poliza = _cargar_movimientos_de_polizas(con, poliza_ids)
        _validar_cuentas_banco(polizas, movimientos_por_poliza)

        cfdis_por_movimiento = cargar_cfdis_por_movimiento(
            con, [m["id"] for m in movimientos_por_poliza.values()])
        cfdi_ids = {c["cfdi_id"] for lista in cfdis_por_movimiento.values() for c in lista}
        impuestos_por_cfdi = _cargar_impuestos_cfdi(con, cfdi_ids)

        movimientos_poliza = []
        for poliza in polizas:
            movimiento = movimientos_por_poliza.get(poliza["id"])
            cfdis = cfdis_por_movimiento.get(movimiento["id"], []) if movimiento else []
            movimientos_poliza.append(_armar_movimiento_poliza(
                poliza, lineas_por_poliza.get(poliza["id"], []), movimiento,
                cfdis, impuestos_por_cfdi, empresa))

        resultado = exportar_polizas_contpaqi_txt(
            movimientos_poliza, ruta_salida, nombre_empresa=empresa["nombre"])

        if marcar_enviadas:
            con.execute(
                f"UPDATE polizas SET enviada_contpaqi = 1 WHERE empresa_id = ? AND id IN ({_lista_ids(poliza_ids)})",
                (empresa_id,),
            )
    return resultado
