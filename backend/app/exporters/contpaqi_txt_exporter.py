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
Exportador de pólizas al layout de texto de importación de Contpaqi.
Genera un .txt de ancho fijo por columna. La tabla de posiciones vive en
`_r7f3a.dat` (codificada); ver `_notas_r7f3a.md` para la referencia interna
de mantenimiento. No editar offsets directamente en este archivo.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import uuid as uuid_lib
from dataclasses import dataclass
from typing import Literal, Optional

from contpaqi_exporter import (
    ConfiguracionCatalogoImpuestos,
    FacturaAplicada,
    ImpuestoPoliza,
    MovimientoPoliza,
)

_DIR = os.path.dirname(__file__)
_TOLERANCIA_AJUSTE = 5.00  # pesos máximos que se autoajustan sin intervención
_LLAVE = b"OC-7f-poliza-2026"
_LLAVE_MARCA = hashlib.sha256(b"OrangeCrew-marca-agua-2026").digest()

def _cargar_mapa() -> dict:
    with open(os.path.join(_DIR, "_r7f3a.dat")) as f:
        blob = f.read().strip()
    xored = base64.b85decode(blob)
    raw = bytes(b ^ _LLAVE[i % len(_LLAVE)] for i, b in enumerate(xored))
    return json.loads(raw)

_MAPA = _cargar_mapa()

def _pos(tipo: str, i: int) -> tuple[int, int]:
    a, b = _MAPA[tipo][i]
    return a, b

def _huella(nombre_empresa: Optional[str]) -> str:
    """12 caracteres hex fijos por RFC (marca de agua). Si algún día
    aparece una póliza filtrada de un despliegue no autorizado, esto
    permite probar de qué instalación salió, incluso si el archivo ya
    no trae ningún otro dato identificable."""
    dato = (nombre_empresa or "SIN_EMPRESA").strip().upper().encode()
    return hmac.new(_LLAVE_MARCA, dato, hashlib.sha256).hexdigest()[:12]

def _nuevo_guid(nombre_empresa: Optional[str] = None) -> str:
    aleatorio = uuid_lib.uuid4().hex
    cuerpo = aleatorio[:20] + _huella(nombre_empresa)
    return f"{cuerpo[0:8]}-{cuerpo[8:12]}-{cuerpo[12:16]}-{cuerpo[16:20]}-{cuerpo[20:32]}".upper()

class ErrorPolizaDescuadrada(ValueError):
    """La póliza no cuadra (cargos != abonos, o IVA/base no reconcilian)
    y no se pudo corregir de forma segura con un ajuste automático."""

@dataclass
class CuentasAjuste:
    """Cuentas contables para la línea de ajuste que se inserta cuando el
    monto bancario conciliado difiere del total del CFDI por una
    diferencia menor (comisión, redondeo, etc.). Debe configurarse por
    empresa contra su propio catálogo de Contpaqi."""
    cuenta_cargo: str = "5020604002"
    cuenta_abono: str = "5020604002"
    descripcion: str = "AJUSTE"

def _leer_plantilla(nombre: str) -> list[str]:
    ruta = os.path.join(_DIR, nombre)
    with open(ruta, encoding="utf-8") as f:
        return [linea.rstrip("\n") for linea in f]

_PLANTILLA_RECIBIDA = _leer_plantilla("_plantilla_recibida.txt")
_PLANTILLA_EMITIDA = _leer_plantilla("_plantilla_emitida.txt")

def _fila_por_tipo(plantilla: list[str], prefijo: str, ocurrencia: int = 0) -> str:
    """Regresa el renglón N que empieza con `prefijo` dentro de la plantilla."""
    encontrados = [l for l in plantilla if l.split(" ", 1)[0] == prefijo]
    return encontrados[ocurrencia]

_TPL_P = _fila_por_tipo(_PLANTILLA_RECIBIDA, "P")
_TPL_M1 = _fila_por_tipo(_PLANTILLA_RECIBIDA, "M1")
_TPL_AM = _fila_por_tipo(_PLANTILLA_RECIBIDA, "AM")
_TPL_I_EGRESO = _fila_por_tipo(_PLANTILLA_RECIBIDA, "I")
_TPL_W2 = _fila_por_tipo(_PLANTILLA_RECIBIDA, "W2")
_TPL_V = _fila_por_tipo(_PLANTILLA_RECIBIDA, "V")
_TPL_AD = _fila_por_tipo(_PLANTILLA_RECIBIDA, "AD")

_TPL_I_INGRESO = _fila_por_tipo(_PLANTILLA_EMITIDA, "I")
_TPL_R = _fila_por_tipo(_PLANTILLA_EMITIDA, "R")
_TPL_E = _fila_por_tipo(_PLANTILLA_EMITIDA, "E")
_TPL_D = _fila_por_tipo(_PLANTILLA_EMITIDA, "D")
_TPL_C = _fila_por_tipo(_PLANTILLA_EMITIDA, "C")

def _num(valor: float) -> str:
    """Misma representación que usa el layout original: float sin ceros
    de más a la derecha (713422.72, pero 16.0 y no 16.00)."""
    return str(round(float(valor), 2))

def _num_tasa(valor: float) -> str:
    """Igual que _num pero con 4 decimales: hay tasas de retención que
    no son exactas a 2 decimales (ej. 10.6667 = 2/3 de 16%, retención de
    IVA por honorarios). Redondear a 2 aquí perdía precisión real."""
    return str(round(float(valor), 4))

def _splice(renglon: str, start: int, end: int, valor: str, justify: Literal["left", "right"],
            permitir_truncar: bool = False) -> str:
    ancho = end - start
    if len(valor) > ancho:
        if not permitir_truncar:
            raise ErrorPolizaDescuadrada(
                f"El valor '{valor}' (largo {len(valor)}) no cabe en la columna "
                f"[{start}:{end}] (ancho {ancho}) del layout de Contpaqi; "
                f"se detiene la exportación para no generar un archivo corrupto."
            )
        # Solo se usa para el folio de referencia del CFDI (dato de
        # consulta, no la llave real de conciliación — esa es el UUID):
        # mejor truncar y dejar la póliza pasar que tronar el lote
        # completo por un folio inusualmente largo.
        valor = valor[-ancho:] if justify == "right" else valor[:ancho]
    relleno = valor.ljust(ancho) if justify == "left" else valor.rjust(ancho)
    return renglon[:start] + relleno + renglon[end:]

def _fila_p(mov: MovimientoPoliza, nombre_empresa: Optional[str]) -> str:
    r = _TPL_P
    r = _splice(r, *_pos("P", 0), mov.fecha.strftime("%Y%m%d"), "left")
    tipo_cod = "1" if mov.tipo == "ingreso" else ("3" if mov.tipo == "diario" else "2")
    r = _splice(r, *_pos("P", 1), tipo_cod, "left")
    r = _splice(r, *_pos("P", 2), str(mov.numero_poliza), "right")
    
    tiene_cfdi = bool(getattr(mov, "cfdi_uuid", None) or getattr(mov, "facturas", None))
    tiene_iva = getattr(mov, "tiene_iva", False)
    if (not tiene_cfdi) and tiene_iva:
        xml_tag = " (ADJUNTAR XML)"
    else:
        xml_tag = " (OPE sistema)"
    desc_limpia = re.sub(r"\s*\((?:adjuntar\s*xml|ope\s*sistema)\)", "", mov.descripcion or "", flags=re.IGNORECASE).strip()
    # Se usa 40, 140 en lugar de _pos("P", 3) para preservar el espacio en la posición 140
    # y evitar que conceptos muy largos se peguen al "Sistema Origen" (posición 141) provocando un error en Contpaqi.
    r = _splice(r, 40, 140, f"{desc_limpia}{xml_tag}", "left", permitir_truncar=True)
    r = _splice(r, *_pos("P", 4), _nuevo_guid(nombre_empresa), "left")
    return r

# --------------------------------------------------------------------------
def _fila_m1(mov: MovimientoPoliza, linea, referencia: str, nombre_empresa: Optional[str]) -> str:
    r = _TPL_M1
    cuenta_texto = str(linea.cuenta).replace("-", "")
    r = _splice(r, *_pos("M1", 0), cuenta_texto, "left")
    # Se usa 34, 64 en lugar de _pos("M1", 1) para preservar el espacio en la posicin 64
    r = _splice(r, 34, 64, referencia, "left", permitir_truncar=True)
    r = _splice(r, *_pos("M1", 2), "0" if linea.naturaleza == "cargo" else "1", "left")
    r = _splice(r, *_pos("M1", 3), _num(abs(linea.importe)), "left")
    r = _splice(r, *_pos("M1", 4), _nuevo_guid(nombre_empresa), "left")
    r = _splice(r, *_pos("M1", 5), mov.fecha.strftime("%Y%m%d"), "left")
    return r

def _fila_am(uuid_cfdi: str) -> str:
    return _splice(_TPL_AM, *_pos("AM", 0), uuid_cfdi, "left")

def _fila_ad(uuid_cfdi: str) -> str:
    return _splice(_TPL_AD, *_pos("AD", 0), uuid_cfdi, "left")

# --------------------------------------------------------------------------
def _fila_i(mov: MovimientoPoliza, factura: FacturaAplicada, impuesto: ImpuestoPoliza,
            cat: ConfiguracionCatalogoImpuestos, es_egreso: bool, nombre_empresa: Optional[str],
            uuid_padre: Optional[str] = None, uuid_propio_forzado: Optional[str] = None) -> tuple[str, str]:
    """Regresa (renglon, uuid_propio). `uuid_padre` es el guid interno de
    la línea de traslado principal de esta misma factura — solo se llena
    en las líneas de RETENCIÓN, para que Contpaqi las pueda ligar a la
    línea de la que se retuvieron (así viene en pólizas reales con
    retenciones: ISR/IVA retenido cada uno en su propio renglón 'I',
    apuntando de vuelta al renglón del IVA trasladado). `uuid_propio_forzado`
    se usa para la línea principal, generado ANTES de escribir sus
    retenciones (que van primero en el archivo, igual que en Contpaqi),
    para que ya exista el valor a referenciar."""
    r = _TPL_I_EGRESO if es_egreso else _TPL_I_INGRESO
    # En una línea de retención, el "total" es el importe retenido tal
    # cual (no tiene sentido base+importe ahí); confirmado con póliza
    # real de retenciones.
    total = impuesto.importe if impuesto.es_retencion else round(impuesto.base + impuesto.importe, 2)
    uuid_propio = uuid_propio_forzado or _nuevo_guid(nombre_empresa)

    if mov.id_persona:
        r = _splice(r, *_pos("I", 0), mov.id_persona, "left")
    r = _splice(r, *_pos("I", 1), str(mov.fecha.year), "left")
    r = _splice(r, *_pos("I", 2), str(mov.fecha.month), "left")
    r = _splice(r, *_pos("I", 3), str(mov.cuenta_banco or ""), "left")
    r = _splice(r, *_pos("I", 4), factura.serie or "", "left")
    r = _splice(r, *_pos("I", 5), factura.folio or "0", "right", permitir_truncar=True)
    r = _splice(r, *_pos("I", 6), factura.uuid or "", "left")
    r = _splice(r, *_pos("I", 7), _num_tasa(impuesto.tasa * 100), "left")
    r = _splice(r, *_pos("I", 8), _num(impuesto.base), "left")
    r = _splice(r, *_pos("I", 9), _num(impuesto.importe), "left")
    r = _splice(r, *_pos("I", 10), _num(total), "left")

    # La base sólo se repite en la línea PRINCIPAL de traslado (nunca en
    # una de retención); confirmado con póliza real de retenciones.
    es_linea_principal = not impuesto.es_retencion
    if es_egreso:
        r = _splice(r, *_pos("I", 11), "0.0", "left")
        r = _splice(r, *_pos("I", 12), _num(impuesto.base) if es_linea_principal else "0.0", "left")
    else:
        r = _splice(r, *_pos("I", 11), _num(impuesto.base) if es_linea_principal else "0.0", "left")
        r = _splice(r, *_pos("I", 12), "0.0", "left")
    r = _splice(r, *_pos("I", 13), uuid_propio, "left")
    if impuesto.es_retencion and uuid_padre:
        r = _splice(r, *_pos("I", 14), uuid_padre, "left")

    if es_egreso and es_linea_principal:
        r = _splice(r, *_pos("I", 15), cat.concepto_iva, "left")
        r = _splice(r, *_pos("I", 16), cat.subconcepto_iva, "left")
        r = _splice(r, *_pos("I", 17), "1", "left")
    else:
        r = _splice(r, *_pos("I", 15), "0", "left")
        r = _splice(r, *_pos("I", 16), "0", "left")
        r = _splice(r, *_pos("I", 17), "0", "left")

    r = _splice(r, *_pos("I", 18), "2" if impuesto.es_retencion else "1", "left")
    r = _splice(r, *_pos("I", 19), "1" if impuesto.tipo_impuesto == "ISR" else "2", "left")
    return r, uuid_propio

def _fila_w2(impuesto: ImpuestoPoliza) -> str:
    return _splice(_TPL_W2, *_pos("W2", 0), _num(impuesto.base), "left")

def _fila_v(mov: MovimientoPoliza, factura: FacturaAplicada, cat: ConfiguracionCatalogoImpuestos) -> str:
    normales = [i for i in factura.impuestos if not i.es_retencion]
    base_total = round(sum(i.base for i in normales), 2)
    iva_total = round(sum(i.importe for i in normales), 2)
    tasa_principal = normales[0].tasa if normales else 0.0
    total_factura = round(base_total + iva_total, 2)

    ret_iva = round(sum(i.importe for i in factura.impuestos if i.es_retencion and i.tipo_impuesto == "IVA"), 2)
    ret_isr = round(sum(i.importe for i in factura.impuestos if i.es_retencion and i.tipo_impuesto == "ISR"), 2)
    neto_a_pagar = round(total_factura - ret_iva - ret_isr, 2)

    r = _TPL_V
    # nota: hay una columna de folio interno de Contpaqi (no CFDI, no
    # número de póliza) que se deja tal cual venía en la plantilla, ver
    # notas internas.
    r = _splice(r, *_pos("V", 0), _num(total_factura), "left")
    r = _splice(r, *_pos("V", 1), _num_tasa(tasa_principal * 100), "left")
    r = _splice(r, *_pos("V", 2), _num(base_total), "left")
    r = _splice(r, *_pos("V", 3), _num(iva_total), "left")
    r = _splice(r, *_pos("V", 4), factura.serie or "", "left")
    r = _splice(r, *_pos("V", 5), factura.folio or "0", "right", permitir_truncar=True)
    r = _splice(r, *_pos("V", 6), _num(total_factura), "left")
    r = _splice(r, *_pos("V", 7), _num(ret_iva), "left")
    r = _splice(r, *_pos("V", 8), _num(ret_isr), "left")
    r = _splice(r, *_pos("V", 9), _num(neto_a_pagar), "left")
    r = _splice(r, *_pos("V", 10), str(mov.fecha.year), "left")
    r = _splice(r, *_pos("V", 11), str(mov.fecha.month), "left")
    r = _splice(r, *_pos("V", 12), str(mov.cuenta_banco or ""), "left")
    r = _splice(r, *_pos("V", 13), factura.uuid or "", "left")
    r = _splice(r, *_pos("V", 14), factura.rfc_persona or "", "left")
    return r

# --------------------------------------------------------------------------
def _fila_r(mov: MovimientoPoliza) -> str:
    r = _TPL_R
    r = _splice(r, *_pos("R", 0), str(mov.fecha.year), "left")
    r = _splice(r, *_pos("R", 1), str(mov.fecha.month), "left")
    return r

def _fila_e() -> str:
    return _TPL_E

def _fila_d(iva_total: float, total: float) -> str:
    r = _TPL_D
    r = _splice(r, *_pos("D", 0), _num(total - iva_total), "left")
    r = _splice(r, *_pos("D", 1), _num(total), "left")
    r = _splice(r, *_pos("D", 2), _num(total - iva_total), "left")
    r = _splice(r, *_pos("D", 3), _num(iva_total), "left")
    return r

def _fila_c() -> str:
    return _TPL_C

# --------------------------------------------------------------------------
# Validación de cuadre — nunca se entrega una póliza descuadrada
# --------------------------------------------------------------------------
_TOLERANCIA_IVA = 1.00  # el CFDI acumula IVA por concepto, no sobre la base total;
# el redondeo por línea puede diferir unos centavos del cálculo base x tasa "en bloque"

def _validar_iva(mov: MovimientoPoliza) -> None:
    for imp in mov.cfdi_impuestos:
        esperado = round(imp.base * imp.tasa, 2)
        if abs(esperado - imp.importe) > _TOLERANCIA_IVA:
            raise ErrorPolizaDescuadrada(
                f"Póliza #{mov.numero_poliza}: IVA no cuadra contra la base "
                f"(base {imp.base} x tasa {imp.tasa} = {esperado}, pero el "
                f"importe de IVA es {imp.importe})."
            )

def _intentar_cuadrar_cargos_abonos(mov: MovimientoPoliza, ajuste: CuentasAjuste) -> None:
    """Si cargos != abonos por una diferencia pequeña (comisión bancaria,
    redondeo del CFDI vs. el movimiento real, 'abono ajuste'), inserta un
    par de líneas de ajuste para cuadrar. Si la diferencia es mayor a la
    tolerancia, se detiene con error en vez de forzar el cuadre a ciegas.
    """
    cargos = sum(l.importe for l in mov.lineas if l.naturaleza == "cargo")
    abonos = sum(l.importe for l in mov.lineas if l.naturaleza == "abono")
    diferencia = round(cargos - abonos, 2)

    if diferencia == 0:
        return

    if abs(diferencia) > _TOLERANCIA_AJUSTE:
        raise ErrorPolizaDescuadrada(
            f"Póliza #{mov.numero_poliza}: cargos ({cargos}) != abonos "
            f"({abonos}), diferencia de {diferencia} supera la tolerancia "
            f"de ajuste automático (${_TOLERANCIA_AJUSTE})."
        )

    from policy_generator import LineaPoliza  # import perezoso, evita ciclo de módulos

    naturaleza_cargo = "abono" if diferencia > 0 else "cargo"
    mov.lineas.append(LineaPoliza(
        cuenta=ajuste.cuenta_abono if diferencia > 0 else ajuste.cuenta_cargo,
        naturaleza=naturaleza_cargo,
        importe=abs(diferencia),
        descripcion=ajuste.descripcion,
        formula_usada="AJUSTE_AUTOMATICO",
    ))

# --------------------------------------------------------------------------
# Construcción de renglones por póliza
# --------------------------------------------------------------------------
def _construir_filas(movimientos: list[MovimientoPoliza], cat: ConfiguracionCatalogoImpuestos,
                      ajuste: CuentasAjuste, nombre_empresa: Optional[str]) -> tuple[list[str], int, int]:
    filas: list[str] = []
    con_cfdi = 0
    sin_cfdi = 0

    for mov in movimientos:
        _validar_iva(mov)
        _intentar_cuadrar_cargos_abonos(mov, ajuste)

        filas.append(_fila_p(mov, nombre_empresa))

        # Una sola factura (campos cfdi_* de siempre) o varias combinadas
        # en un mismo pago (mov.facturas) se tratan igual de aquí en
        # adelante; si vienen ambas, gana `facturas`.
        if mov.facturas:
            facturas = mov.facturas
        elif mov.cfdi_uuid or mov.cfdi_impuestos:
            facturas = [FacturaAplicada(uuid=mov.cfdi_uuid or "", serie=mov.cfdi_serie or "",
                                         folio=mov.cfdi_folio or "", impuestos=mov.cfdi_impuestos)]
        else:
            facturas = []

        es_multiple = len(facturas) > 1
        if es_multiple:
            folios = []
            for fac in facturas:
                if fac.folio and fac.folio.strip():
                    folios.append(fac.folio.strip())
                else:
                    folios.append("U" + fac.uuid[-4:])
            referencia = ", ".join(folios)[:50]
        else:
            referencia = (mov.numero_factura or "").strip()
        es_egreso = mov.tipo == "egreso"
        uuids = [f.uuid for f in facturas if f.uuid]

        for idx, linea in enumerate(mov.lineas):
            filas.append(_fila_m1(mov, linea, referencia, nombre_empresa))
            if not es_egreso and idx == 0:
                filas.append(_fila_r(mov))
            if uuids:
                for u in uuids:
                    filas.append(_fila_am(u))

        if not getattr(mov, "afectar_iva_fiscal", True):
            for u in uuids:
                filas.append(_fila_ad(u))
            continue

        impuestos_totales = [imp for f in facturas for imp in f.impuestos]
        if not impuestos_totales:
            sin_cfdi += 1
            continue

        if not mov.cuenta_banco:
            raise ErrorPolizaDescuadrada(
                f"Póliza #{mov.numero_poliza} afecta impuestos (F4/F6) "
                f"pero no trae cuenta_banco, necesaria para las filas 'I'/'V'."
            )

        if es_egreso:
            # Confirmado con ejemplos reales: I/W2/V se repiten completos
            # POR factura. Dentro de una factura, las líneas de RETENCIÓN
            # (si las hay) van PRIMERO en el archivo, y la de traslado
            # (IVA normal) + su W2 van al final — así viene en la póliza
            # real con retenciones del usuario. Por eso el GUID de la
            # línea principal se genera antes, para que las retenciones
            # ya lo puedan referenciar aunque se escriban primero.
            for factura in facturas:
                principal = next((i for i in factura.impuestos if not i.es_retencion), None)
                uuid_principal = _nuevo_guid(nombre_empresa) if principal else None
                for imp in factura.impuestos:
                    if not imp.es_retencion:
                        continue
                    fila_i, _ = _fila_i(mov, factura, imp, cat, es_egreso=True,
                                         nombre_empresa=nombre_empresa, uuid_padre=uuid_principal)
                    filas.append(fila_i)
                if principal:
                    fila_i, _ = _fila_i(mov, factura, principal, cat, es_egreso=True,
                                         nombre_empresa=nombre_empresa, uuid_propio_forzado=uuid_principal)
                    filas.append(fila_i)
                    filas.append(_fila_w2(principal))
                if factura.impuestos:
                    filas.append(_fila_v(mov, factura, cat))
        else:
            # Mismo orden que en egreso (retenciones antes que la
            # principal). E/D/C van UNA sola vez para todo el pago, con
            # los importes de traslado (no de retención) de todas las
            # facturas sumados — el lado ingreso con retención no se ha
            # visto todavía en ningún ejemplo real; avisar antes de
            # confiar en esto si aparece.
            for factura in facturas:
                principal = next((i for i in factura.impuestos if not i.es_retencion), None)
                uuid_principal = _nuevo_guid(nombre_empresa) if principal else None
                for imp in factura.impuestos:
                    if not imp.es_retencion:
                        continue
                    fila_i, _ = _fila_i(mov, factura, imp, cat, es_egreso=False,
                                         nombre_empresa=nombre_empresa, uuid_padre=uuid_principal)
                    filas.append(fila_i)
                if principal:
                    fila_i, _ = _fila_i(mov, factura, principal, cat, es_egreso=False,
                                         nombre_empresa=nombre_empresa, uuid_propio_forzado=uuid_principal)
                    filas.append(fila_i)
            normales_totales = [i for i in impuestos_totales if not i.es_retencion]
            total_agg = round(sum(i.base + i.importe for i in normales_totales), 2)
            iva_agg = round(sum(i.importe for i in normales_totales), 2)
            filas.append(_fila_e())
            filas.append(_fila_d(iva_agg, total_agg))
            filas.append(_fila_c())

        for u in uuids:
            filas.append(_fila_ad(u))
        con_cfdi += 1

    return filas, con_cfdi, sin_cfdi

def exportar_polizas_contpaqi_txt(
    movimientos_poliza: list[MovimientoPoliza],
    ruta_salida: str,
    catalogo_impuestos: Optional[ConfiguracionCatalogoImpuestos] = None,
    cuentas_ajuste: Optional[CuentasAjuste] = None,
    nombre_empresa: Optional[str] = None,
) -> dict:
    """Genera el archivo .txt de importación de pólizas de Contpaqi
    (layout fijo por columna, no Excel). Levanta ErrorPolizaDescuadrada
    si alguna póliza no cuadra y no se puede corregir de forma segura;
    en ese caso NO se escribe ningún archivo.

    `nombre_empresa` se usa solo para la marca de agua interna en los GUID
    que no son del CFDI (ver `_huella`); no cambia nada visible ni afecta
    la carga en Contpaqi."""
    if not ruta_salida.lower().endswith(".txt"):
        ruta_salida = os.path.splitext(ruta_salida)[0] + ".txt"

    cat = catalogo_impuestos or ConfiguracionCatalogoImpuestos()
    ajuste = cuentas_ajuste or CuentasAjuste()

    filas, con_cfdi, sin_cfdi = _construir_filas(movimientos_poliza, cat, ajuste, nombre_empresa)

    try:
        with open(ruta_salida, "w", encoding="cp1252", errors="replace", newline="\r\n") as f:
            f.write("\n".join(filas) + "\n")
    except OSError as e:
        raise ErrorPolizaDescuadrada(f"No se pudo escribir el archivo '{ruta_salida}': {e}") from e

    return {
        "archivo": ruta_salida,
        "formato": "txt",
        "polizas": len(movimientos_poliza),
        "polizas_con_cfdi": con_cfdi,
        "polizas_sin_cfdi": sin_cfdi,
        "filas_totales": len(filas),
    }
