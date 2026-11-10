import os
import sys
from decimal import Decimal

try:
    import pytest
except ImportError:
    pytest = None

_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in [
    os.path.join(_DIR, "..", "app", "core"),
    os.path.join(_DIR, "..", "backend", "app", "core"),
    os.path.join(_DIR, "..", "webapp"),
    os.path.join(_DIR, "..", "..", "webapp"),
]:
    _norm = os.path.abspath(_p)
    if os.path.isdir(_norm) and _norm not in sys.path:
        sys.path.insert(0, _norm)

from formula_engine import construir_variables, FormulaError
from policy_generator import generar_poliza, LineaPlantilla
from cfdi_asociados import aplicar_impuestos_cfdi, reconciliar_rfc

D = Decimal
CUENTA_BANCO = "1020001"


def movimiento(total, tipo="egreso", **extra):
    base = {"total": D(total), "descripcion": "pago", "tipo": tipo,
            "tiene_iva": True, "cuenta_banco": CUENTA_BANCO}
    base.update(extra)
    return base


def plantilla_egreso(gasto="BASE", iva="IVA"):
    return [
        LineaPlantilla("BANCO", "abono", "TOTAL"),
        LineaPlantilla("5000", "cargo", gasto),
        LineaPlantilla("1180", "cargo", iva),
    ]


def importe_de(resultado, cuenta):
    return next(l.importe for l in resultado.lineas if l.cuenta == cuenta)


def test_desglose_siempre_suma_al_total():
    for centavos in range(1, 20000):
        total = D(centavos) / 100
        v = construir_variables(total=total)
        assert v["BASE"] + v["IVA"] - v["RET_IVA"] - v["RET_ISR"] == total


def test_formulas_con_division_cuadran_y_no_tocan_el_banco():
    plantilla = plantilla_egreso("TOTAL / 1.16", "TOTAL * 0.16 / 1.16")
    for centavos in range(100, 30000):
        total = D(centavos) / 100
        r = generar_poliza(movimiento(total), plantilla, "regla", "m")
        assert r.cuadrada, (total, r.diferencia)
        assert importe_de(r, CUENTA_BANCO) == total


def test_cargos_con_un_centavo_de_mas_se_absorben_sin_tocar_el_banco():
    plantilla = plantilla_egreso("TOTAL / 1.16 + 0.01", "TOTAL - TOTAL / 1.16")
    r = generar_poliza(movimiento("1000.00"), plantilla, "regla", "m")
    assert r.cuadrada
    assert importe_de(r, CUENTA_BANCO) == D("1000.00")


def test_cfdi_con_retenciones_ignora_tasas_de_la_empresa():
    plantilla = plantilla_egreso() + [
        LineaPlantilla("2160", "abono", "RET_IVA"),
        LineaPlantilla("2170", "abono", "RET_ISR"),
    ]
    mov = movimiento("9533.33", iva_exacto=D("1600.00"), ret_iva=D("1066.67"), ret_isr=D("1000.00"))
    r = generar_poliza(mov, plantilla, "honorarios", "m")
    assert r.cuadrada
    assert importe_de(r, "5000") == D("10000.00")


def test_cfdi_sin_retenciones_no_inventa_retenciones_por_tasa():
    v = construir_variables(total=D("1160.00"), iva_exacto=D("160.00"),
                            tasa_ret_iva=D("0.106667"), tasa_ret_isr=D("0.10"))
    assert v["RET_IVA"] == 0 and v["RET_ISR"] == 0
    assert v["BASE"] == D("1000.00")


def test_retencion_capturada_a_mano_despeja_la_base():
    v = construir_variables(total=D("9533.33"), ret_iva=D("1066.67"), ret_isr=D("1000.00"))
    assert v["BASE"] == D("10000.00")
    assert v["IVA"] == D("1600.00")


def test_retencion_estimada_por_tasa_cuadra():
    v = construir_variables(total=D("9533.33"), tasa_ret_iva=D("0.106667"), tasa_ret_isr=D("0.10"))
    assert v["BASE"] + v["IVA"] - v["RET_IVA"] - v["RET_ISR"] == D("9533.33")


def test_sin_iva_omite_la_linea_en_cero():
    mov = movimiento("500.00", tiene_iva=False)
    r = generar_poliza(mov, plantilla_egreso(), "traspaso", "m")
    assert r.cuadrada
    assert len(r.lineas) == 2


def test_banco_sin_cuenta_contable_no_cuadra():
    mov = movimiento("1160.00")
    mov["cuenta_banco"] = None
    r = generar_poliza(mov, plantilla_egreso(), "regla", "m")
    assert not r.cuadrada
    assert any("cuenta contable de banco" in e for e in r.errores)


def test_plantilla_vacia_no_cuadra():
    r = generar_poliza(movimiento("100.00"), [], "regla", "m")
    assert not r.cuadrada


def test_regla_de_egreso_aplicada_a_ingreso_se_rechaza():
    r = generar_poliza(movimiento("1160.00", tipo="ingreso"), plantilla_egreso(), "regla", "m")
    assert not r.cuadrada
    assert any("no corresponde a este movimiento" in e for e in r.errores)


def test_regla_ligada_a_otro_banco_se_rechaza():
    plantilla = [
        LineaPlantilla("1020002", "abono", "TOTAL"),
        LineaPlantilla("5000", "cargo", "TOTAL"),
    ]
    r = generar_poliza(movimiento("100.00"), plantilla, "regla", "m",
                       cuentas_banco_empresa=frozenset({CUENTA_BANCO, "1020002"}))
    assert not r.cuadrada
    assert any("otro banco" in e for e in r.errores)


def test_total_en_cero_no_genera_poliza():
    r = generar_poliza(movimiento("0.00"), plantilla_egreso(), "regla", "m")
    assert not r.cuadrada


def cfdi(**extra):
    base = {"cfdi_total": D("1160.00"), "importe_aplicado": D("1160.00"), "iva_cfdi": D("160.00"),
            "ret_iva_cfdi": D("0"), "ret_isr_cfdi": D("0"), "cfdi_tipo": "recibido",
            "rfc_emisor": "AAA010101AAA", "rfc_receptor": "EMP010101EMP"}
    base.update(extra)
    return base


def test_pago_parcial_toma_iva_proporcional():
    mov = movimiento("580.00")
    aplicar_impuestos_cfdi(mov, [cfdi(importe_aplicado=D("580.00"))])
    assert mov["iva_exacto"] == D("80.00")


def test_complemento_de_pago_no_fija_iva_cero():
    mov = movimiento("1160.00")
    aplicar_impuestos_cfdi(mov, [cfdi(cfdi_total=D("0"), iva_cfdi=D("0"))])
    assert "iva_exacto" not in mov


def test_cfdi_que_no_cubre_el_movimiento_deja_nota():
    mov = movimiento("2000.00")
    aplicar_impuestos_cfdi(mov, [cfdi()])
    assert mov["notas"]


def test_rfc_del_cfdi_alimenta_al_movimiento():
    mov = movimiento("1160.00", rfc_contraparte=None)
    assert reconciliar_rfc(mov, [cfdi()]) is None
    assert mov["rfc_contraparte"] == "AAA010101AAA"


def test_rfc_distinto_se_detecta_como_conflicto():
    mov = movimiento("1160.00", rfc_contraparte="ZZZ010101ZZZ")
    assert "no coincide" in reconciliar_rfc(mov, [cfdi()])


def test_cfdi_de_varias_contrapartes_es_conflicto():
    mov = movimiento("2320.00")
    otro = cfdi(rfc_emisor="BBB010101BBB")
    assert reconciliar_rfc(mov, [cfdi(), otro]) is not None
