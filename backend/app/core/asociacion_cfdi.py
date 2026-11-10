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
Motor de asociación entre movimientos bancarios y CFDI (lógica pura, sin base de datos).

Un movimiento solo se asocia SOLO cuando se cumplen a la vez:
  1. la dirección es correcta (egreso con CFDI recibido, ingreso con CFDI emitido);
  2. hay evidencia de identidad de la contraparte: RFC, cuenta bancaria ya ligada a
     ese RFC, o el UUID del CFDI dentro del movimiento;
  3. el importe coincide con el saldo pendiente del CFDI;
  4. solo un CFDI cumple lo anterior (o solo uno trae además su folio o UUID);
  5. ningún otro movimiento del mismo lote reclama ese mismo CFDI.

Todo lo demás (folio sin RFC, nombre parecido, pagos parciales, un pago contra
varias facturas, moneda extranjera, factura fechada después del pago) es
sugerencia y requiere confirmación humana. El importe por sí solo nunca basta.
"""

import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, datetime
from decimal import Decimal
from itertools import combinations
from typing import Iterable, Mapping, Optional

try:
    from .formula_engine import redondear_moneda
except ImportError:  # se ejecuta como script suelto
    from formula_engine import redondear_moneda

NIVEL_AUTOMATICO = "automatico"
NIVEL_SUGERENCIA = "sugerencia"

TOLERANCIA_IMPORTE = Decimal("0.01")
DIAS_FACTURA_POSTERIOR_TOLERADOS = 5
DIAS_ANTIGUEDAD_AVISO = 400
MAX_CANDIDATOS_COMBINACION = 12
MAX_FACTURAS_POR_COMBINACION = 4
MAX_PROPUESTAS_ALTERNAS = 3
MIN_CARACTERES_FOLIO_NUMERICO = 3
MIN_DIGITOS_CUENTA = 10
RFCS_GENERICOS = frozenset({"XAXX010101000", "XEXX010101000"})
PALABRAS_SOCIETARIAS = frozenset({"SAPI", "SOFOM", "SRL", "LTD", "INC", "LLC", "CORP"})


@dataclass(frozen=True)
class Propuesta:
    movimiento_id: int
    aplicaciones: tuple          # ((cfdi_id, importe), ...)
    nivel: str
    puntaje: int
    razones: tuple
    advertencias: tuple = ()


@dataclass(frozen=True)
class ResultadoAsociacion:
    movimiento_id: int
    propuestas: tuple
    descartados: tuple = ()      # ((cfdi_id, motivo), ...) CFDI con el importe pero otra contraparte


@dataclass(frozen=True)
class _Cfdi:
    id: int
    uuid: str
    tipo: str
    comprobante: str
    rfc: str
    nombre: str
    saldo: Decimal
    fecha: Optional[date]
    serie: str
    folio: str
    metodo_pago: str
    moneda: str


@dataclass(frozen=True)
class _Evidencia:
    rfc: bool = False
    cuenta: bool = False
    uuid: bool = False
    folio: bool = False
    nombre: bool = False

    @property
    def identidad_fuerte(self) -> bool:
        return self.rfc or self.cuenta or self.uuid

    @property
    def alguna(self) -> bool:
        return self.rfc or self.cuenta or self.uuid or self.folio or self.nombre

    def unir(self, otra: "_Evidencia") -> "_Evidencia":
        return _Evidencia(self.rfc or otra.rfc, self.cuenta or otra.cuenta, self.uuid or otra.uuid,
                          self.folio or otra.folio, self.nombre or otra.nombre)


def _fecha(valor) -> Optional[date]:
    if valor in (None, ""):
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    try:
        return datetime.strptime(str(valor)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def _rfc(valor) -> str:
    return (valor or "").strip().upper()


def _cuenta(valor) -> str:
    digitos = re.sub(r"\D", "", str(valor or ""))
    return digitos if len(digitos) >= MIN_DIGITOS_CUENTA else ""


def _ascii_mayusculas(texto) -> str:
    plano = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode("ascii")
    return plano.upper()


def _tokens(*textos) -> list:
    return re.findall(r"[A-Z0-9]+", _ascii_mayusculas(" ".join(str(t) for t in textos if t)))


def _centavos(importe: Decimal) -> int:
    return int((importe * 100).to_integral_value())


def _menciona_uuid(tokens: list, uuid: str) -> bool:
    uuid_plano = re.sub(r"[^A-F0-9]", "", uuid.upper())
    return bool(uuid_plano) and any(len(t) >= 12 and t in uuid_plano for t in tokens)


def _menciona_folio(tokens: list, cfdi: _Cfdi) -> bool:
    folio = re.sub(r"[^A-Z0-9]", "", _ascii_mayusculas(cfdi.folio))
    serie = re.sub(r"[^A-Z0-9]", "", _ascii_mayusculas(cfdi.serie))
    if not folio:
        return False
    conjunto = set(tokens)
    combinado = serie + folio
    if serie and len(combinado) >= MIN_CARACTERES_FOLIO_NUMERICO:
        if combinado in conjunto:
            return True
        if any(t == serie and tokens[i + 1] == folio for i, t in enumerate(tokens[:-1])):
            return True
    if len(folio) < MIN_CARACTERES_FOLIO_NUMERICO:
        return False
    if folio in conjunto:
        return True
    return any(len(t) > len(folio) and t.endswith(folio) and t[:-len(folio)].isalpha() for t in tokens)


def _nombre_parecido(conjunto_tokens: set, nombre: str) -> bool:
    palabras = [p for p in _tokens(nombre) if len(p) >= 4 and p not in PALABRAS_SOCIETARIAS]
    if not palabras:
        return False
    coinciden = sum(1 for p in palabras if p in conjunto_tokens)
    return coinciden >= min(2, len(palabras))


def _normalizar_cfdi(crudo) -> Optional[_Cfdi]:
    estatus = str(crudo.get("estatus") or crudo.get("estado") or "").strip().lower()
    if crudo.get("cancelado") or estatus == "cancelado":
        return None
    tipo = crudo.get("tipo")
    es_recibido = tipo == "recibido"
    total = redondear_moneda(crudo["total"])
    saldo = redondear_moneda(crudo["saldo_pendiente"]) if crudo.get("saldo_pendiente") is not None else total
    if saldo <= 0:
        return None
    return _Cfdi(
        id=crudo["id"],
        uuid=str(crudo.get("uuid") or ""),
        tipo=tipo,
        comprobante=str(crudo.get("tipo_comprobante") or "I").strip().upper(),
        rfc=_rfc(crudo["rfc_emisor"] if es_recibido else crudo["rfc_receptor"]),
        nombre=str((crudo.get("nombre_emisor") if es_recibido else crudo.get("nombre_receptor")) or ""),
        saldo=saldo,
        fecha=_fecha(crudo.get("fecha")),
        serie=str(crudo.get("serie") or ""),
        folio=str(crudo.get("folio") or ""),
        metodo_pago=str(crudo.get("metodo_pago") or "").strip().upper(),
        moneda=str(crudo.get("moneda") or "MXN").strip().upper(),
    )


def _direccion_correcta(mov_tipo: str, cfdi: _Cfdi) -> bool:
    """I: egreso con recibido e ingreso con emitido. E (nota de crédito): al revés."""
    if cfdi.comprobante not in ("I", "E") or mov_tipo not in ("ingreso", "egreso"):
        return False
    esperado = "recibido" if mov_tipo == "egreso" else "emitido"
    if cfdi.comprobante == "E":
        esperado = "emitido" if esperado == "recibido" else "recibido"
    return cfdi.tipo == esperado


@dataclass(frozen=True)
class _Movimiento:
    id: int
    tipo: str
    total: Decimal
    fecha: Optional[date]
    rfc: str
    cuenta: str
    tokens: tuple
    conjunto: frozenset


def _preparar_movimiento(mov: dict) -> _Movimiento:
    rfc = _rfc(mov.get("rfc_contraparte"))
    tokens = _tokens(mov.get("descripcion"), mov.get("referencia_bancaria"), mov.get("numero_factura"))
    return _Movimiento(
        id=mov["id"],
        tipo=mov.get("tipo"),
        total=redondear_moneda(mov["total"]),
        fecha=_fecha(mov.get("fecha")),
        rfc="" if rfc in RFCS_GENERICOS else rfc,
        cuenta=_cuenta(mov.get("cuenta_bancaria_contraparte")),
        tokens=tuple(tokens),
        conjunto=frozenset(tokens),
    )


def _avisos_de_fecha_y_moneda(mov: _Movimiento, cfdi: _Cfdi):
    """Regresa (advertencias, impide_automatico, dias_de_diferencia)."""
    advertencias = []
    impide = False
    if cfdi.moneda != "MXN":
        advertencias.append(f"El CFDI está en {cfdi.moneda}: validar el tipo de cambio antes de confirmar.")
        impide = True
    dias = (cfdi.fecha - mov.fecha).days if cfdi.fecha and mov.fecha else None
    if dias is not None:
        if dias > DIAS_FACTURA_POSTERIOR_TOLERADOS:
            advertencias.append(f"El CFDI está fechado {dias} días después del movimiento.")
            impide = True
        elif dias > 0:
            advertencias.append(f"El CFDI está fechado {dias} día(s) después del movimiento.")
        elif dias < -DIAS_ANTIGUEDAD_AVISO:
            advertencias.append("El CFDI tiene más de un año de antigüedad respecto al movimiento.")
    return advertencias, impide, dias


_PUNTOS_AJUSTE = {"exacto": 20, "combinado": 10, "parcial": 5}


def _puntaje(ev: _Evidencia, ajuste: str, dias: Optional[int]) -> int:
    puntos = _PUNTOS_AJUSTE[ajuste]
    puntos += 45 if ev.uuid else 0
    puntos += 40 if ev.rfc else 0
    puntos += (10 if ev.rfc else 35) if ev.cuenta else 0
    puntos += 25 if ev.folio else 0
    puntos += 10 if ev.nombre else 0
    puntos += 5 if dias is not None and abs(dias) <= 30 else 0
    return min(100, puntos)


def _razones(ev: _Evidencia, ajuste: str, cantidad: int = 1) -> tuple:
    razones = []
    if ev.uuid:
        razones.append("El UUID del CFDI aparece en el movimiento.")
    if ev.rfc:
        razones.append("El RFC del movimiento coincide con el del CFDI.")
    if ev.cuenta:
        razones.append("La cuenta bancaria ya estaba ligada a esa contraparte.")
    if ev.folio:
        razones.append("El folio aparece en la descripción o referencia.")
    if ev.nombre:
        razones.append("El nombre de la contraparte aparece en la descripción.")
    razones.append({
        "exacto": "El importe coincide con el saldo pendiente del CFDI.",
        "parcial": "El importe es menor al saldo del CFDI: sería un pago parcial.",
        "combinado": f"La suma de {cantidad} CFDI de la misma contraparte coincide con el importe.",
    }[ajuste])
    return tuple(razones)


class MotorAsociacion:
    """Se construye una vez por lote de CFDI y se consulta por movimiento.

    `cuentas_conocidas` mapea RFC -> cuentas bancarias que ya se confirmaron antes
    para esa contraparte. Sirve como evidencia y también para vetar: una cuenta
    ligada a un proveedor no puede pagar facturas de otro.
    """

    def __init__(self, cfdis: Iterable[dict], cuentas_conocidas: Optional[Mapping] = None):
        self._por_rfc = defaultdict(list)
        self._por_centavos = defaultdict(list)
        for crudo in cfdis:
            cfdi = _normalizar_cfdi(crudo)
            if cfdi is None:
                continue
            self._por_rfc[cfdi.rfc].append(cfdi)
            self._por_centavos[_centavos(cfdi.saldo)].append(cfdi)

        self._cuentas_por_rfc = {}
        self._rfcs_por_cuenta = defaultdict(set)
        for rfc, cuentas in (cuentas_conocidas or {}).items():
            rfc_norm = _rfc(rfc)
            normalizadas = {c for c in (_cuenta(x) for x in cuentas) if c}
            self._cuentas_por_rfc[rfc_norm] = frozenset(normalizadas)
            for cuenta in normalizadas:
                self._rfcs_por_cuenta[cuenta].add(rfc_norm)

    def _candidatos(self, mov: _Movimiento) -> dict:
        rfcs = {mov.rfc} if mov.rfc else set(self._rfcs_por_cuenta.get(mov.cuenta, ()))
        candidatos = {}
        for rfc in rfcs:
            for cfdi in self._por_rfc.get(rfc, ()):
                candidatos[cfdi.id] = cfdi
        centavos = _centavos(mov.total)
        for clave in (centavos - 1, centavos, centavos + 1):
            for cfdi in self._por_centavos.get(clave, ()):
                candidatos[cfdi.id] = cfdi
        return candidatos

    def _veto_de_identidad(self, mov: _Movimiento, cfdi: _Cfdi) -> Optional[str]:
        if mov.rfc and mov.rfc != cfdi.rfc:
            return f"El RFC del movimiento ({mov.rfc}) no es el del CFDI ({cfdi.rfc})."
        duenos = self._rfcs_por_cuenta.get(mov.cuenta)
        if duenos and cfdi.rfc not in duenos:
            return "La cuenta bancaria del movimiento ya pertenece a otra contraparte."
        return None

    def _evidencia(self, mov: _Movimiento, cfdi: _Cfdi) -> _Evidencia:
        return _Evidencia(
            rfc=bool(mov.rfc) and mov.rfc == cfdi.rfc,
            cuenta=bool(mov.cuenta) and mov.cuenta in self._cuentas_por_rfc.get(cfdi.rfc, ()),
            uuid=_menciona_uuid(mov.tokens, cfdi.uuid),
            folio=_menciona_folio(mov.tokens, cfdi),
            nombre=_nombre_parecido(mov.conjunto, cfdi.nombre),
        )

    def proponer(self, movimiento: dict) -> ResultadoAsociacion:
        mov = _preparar_movimiento(movimiento)
        if mov.total <= 0 or mov.tipo not in ("ingreso", "egreso"):
            return ResultadoAsociacion(mov.id, ())

        elegibles, descartados = [], []
        for cfdi in self._candidatos(mov).values():
            if not _direccion_correcta(mov.tipo, cfdi):
                continue
            veto = self._veto_de_identidad(mov, cfdi)
            if veto:
                if abs(cfdi.saldo - mov.total) <= TOLERANCIA_IMPORTE:
                    descartados.append((cfdi.id, veto))
                continue
            elegibles.append((cfdi, self._evidencia(mov, cfdi)))

        propuestas, hay_exacta_fuerte = self._exactas(mov, elegibles)
        if not hay_exacta_fuerte:
            propuestas += self._parciales(mov, elegibles)
            propuestas += self._combinadas(mov, elegibles)
        propuestas.sort(key=lambda p: (p.nivel != NIVEL_AUTOMATICO, -p.puntaje))
        return ResultadoAsociacion(mov.id, tuple(propuestas), tuple(descartados))

    def _exactas(self, mov: _Movimiento, elegibles: list) -> tuple:
        exactas = [(c, ev) for c, ev in elegibles
                   if ev.alguna and abs(mov.total - c.saldo) <= TOLERANCIA_IMPORTE]
        fuertes = [(c, ev) for c, ev in exactas if ev.identidad_fuerte]

        elegido = None
        if len(fuertes) == 1:
            elegido = fuertes[0][0].id
        elif len(fuertes) > 1:
            con_referencia = [c for c, ev in fuertes if ev.folio or ev.uuid]
            if len(con_referencia) == 1:
                elegido = con_referencia[0].id
        ambiguo = len(fuertes) > 1 and elegido is None

        propuestas = []
        for cfdi, ev in exactas:
            advertencias, impide, dias = _avisos_de_fecha_y_moneda(mov, cfdi)
            if ambiguo and ev.identidad_fuerte:
                advertencias.append(f"Hay {len(fuertes)} CFDI de la misma contraparte con ese importe; elegir cuál.")
            if not ev.identidad_fuerte:
                advertencias.append("Sin RFC ni cuenta conocida: la coincidencia depende solo del folio o el nombre.")
            automatico = cfdi.id == elegido and not impide
            propuestas.append(Propuesta(
                movimiento_id=mov.id,
                aplicaciones=((cfdi.id, mov.total),),
                nivel=NIVEL_AUTOMATICO if automatico else NIVEL_SUGERENCIA,
                puntaje=_puntaje(ev, "exacto", dias),
                razones=_razones(ev, "exacto"),
                advertencias=tuple(advertencias),
            ))
        return propuestas, bool(fuertes)

    def _parciales(self, mov: _Movimiento, elegibles: list) -> list:
        propuestas = []
        for cfdi, ev in elegibles:
            if not ev.identidad_fuerte or mov.total >= cfdi.saldo - TOLERANCIA_IMPORTE:
                continue
            advertencias, _, dias = _avisos_de_fecha_y_moneda(mov, cfdi)
            if cfdi.metodo_pago == "PUE":
                advertencias.append("El CFDI es PUE: un pago parcial es inusual, revisar.")
            propuestas.append(Propuesta(
                movimiento_id=mov.id,
                aplicaciones=((cfdi.id, mov.total),),
                nivel=NIVEL_SUGERENCIA,
                puntaje=_puntaje(ev, "parcial", dias),
                razones=_razones(ev, "parcial"),
                advertencias=tuple(advertencias),
            ))
        propuestas.sort(key=lambda p: -p.puntaje)
        return propuestas[:MAX_PROPUESTAS_ALTERNAS]

    def _combinadas(self, mov: _Movimiento, elegibles: list) -> list:
        pool = sorted(
            (c for c, ev in elegibles if ev.identidad_fuerte and c.saldo <= mov.total + TOLERANCIA_IMPORTE),
            key=lambda c: (c.fecha or date.max, c.id),
        )[:MAX_CANDIDATOS_COMBINACION]
        evidencias = {c.id: ev for c, ev in elegibles}

        encontradas = []
        for cantidad in range(2, MAX_FACTURAS_POR_COMBINACION + 1):
            for grupo in combinations(pool, cantidad):
                if abs(sum(c.saldo for c in grupo) - mov.total) <= TOLERANCIA_IMPORTE:
                    encontradas.append(grupo)
        encontradas.sort(key=lambda g: (len(g), sum((c.fecha or date.max).toordinal() for c in g)))

        propuestas = []
        for grupo in encontradas[:MAX_PROPUESTAS_ALTERNAS]:
            importes = [c.saldo for c in grupo]
            importes[-1] += mov.total - sum(importes)
            ev = _Evidencia()
            advertencias = []
            if len(encontradas) > 1:
                advertencias.append(f"Hay {len(encontradas)} combinaciones posibles de CFDI; elegir la correcta.")
            for cfdi in grupo:
                ev = ev.unir(evidencias[cfdi.id])
                extra, _, _ = _avisos_de_fecha_y_moneda(mov, cfdi)
                advertencias.extend(a for a in extra if a not in advertencias)
            propuestas.append(Propuesta(
                movimiento_id=mov.id,
                aplicaciones=tuple((c.id, i) for c, i in zip(grupo, importes)),
                nivel=NIVEL_SUGERENCIA,
                puntaje=_puntaje(ev, "combinado", None),
                razones=_razones(ev, "combinado", len(grupo)),
                advertencias=tuple(advertencias),
            ))
        return propuestas

    def proponer_lote(self, movimientos: Iterable[dict]) -> dict:
        """Proponer para varios movimientos y degradar los automáticos que se pisan."""
        resultados = {m["id"]: self.proponer(m) for m in movimientos}

        reclamos = defaultdict(set)
        for mov_id, resultado in resultados.items():
            for p in resultado.propuestas:
                if p.nivel == NIVEL_AUTOMATICO:
                    for cfdi_id, _ in p.aplicaciones:
                        reclamos[cfdi_id].add(mov_id)
        en_disputa = {cfdi_id: ids for cfdi_id, ids in reclamos.items() if len(ids) > 1}
        if not en_disputa:
            return resultados

        for mov_id, resultado in resultados.items():
            nuevas = []
            for p in resultado.propuestas:
                disputados = [c for c, _ in p.aplicaciones if c in en_disputa]
                if p.nivel == NIVEL_AUTOMATICO and disputados:
                    otros = sorted({i for c in disputados for i in en_disputa[c]} - {mov_id})
                    p = replace(p, nivel=NIVEL_SUGERENCIA, advertencias=p.advertencias + (
                        f"El mismo CFDI coincide también con los movimientos {otros}; confirmar cuál corresponde.",))
                nuevas.append(p)
            nuevas.sort(key=lambda p: (p.nivel != NIVEL_AUTOMATICO, -p.puntaje))
            resultados[mov_id] = replace(resultado, propuestas=tuple(nuevas))
        return resultados
