import os
import sys
import time
from decimal import Decimal

_DIR = os.path.dirname(os.path.abspath(__file__))
for _p in [
    os.path.join(_DIR, "..", "app", "core"),
    os.path.join(_DIR, "..", "backend", "app", "core"),
]:
    _norm = os.path.abspath(_p)
    if os.path.isdir(_norm) and _norm not in sys.path:
        sys.path.insert(0, _norm)

from asociacion_cfdi import MotorAsociacion, NIVEL_AUTOMATICO, NIVEL_SUGERENCIA

D = Decimal
RFC_A = "AAA010101AAA"
RFC_B = "BBB010101BBB"
EMPRESA = "EMP010101EMP"


def cfdi(id, total, rfc=RFC_A, tipo="recibido", **extra):
    base = {
        "id": id, "uuid": f"6F3C1A2B-0000-4000-8000-{id:012d}", "tipo": tipo, "total": D(total),
        "rfc_emisor": rfc if tipo == "recibido" else EMPRESA,
        "rfc_receptor": EMPRESA if tipo == "recibido" else rfc,
        "nombre_emisor": "Proveedor Alfa Industrial SA de CV" if tipo == "recibido" else "Demo SA",
        "nombre_receptor": "Demo SA" if tipo == "recibido" else "Cliente Alfa Industrial SA de CV",
        "serie": "A", "folio": str(1000 + id), "fecha": "2026-09-20", "metodo_pago": "PPD",
    }
    base.update(extra)
    return base


def mov(id, total, tipo="egreso", **extra):
    base = {"id": id, "total": D(total), "tipo": tipo, "fecha": "2026-10-05",
            "descripcion": "SPEI PAGO", "rfc_contraparte": None, "cuenta_bancaria_contraparte": None}
    base.update(extra)
    return base


def unica(resultado):
    assert len(resultado.propuestas) == 1, resultado.propuestas
    return resultado.propuestas[0]


def test_rfc_e_importe_exactos_se_asocian_solos():
    r = MotorAsociacion([cfdi(1, "1160.00")]).proponer(mov(1, "1160.00", rfc_contraparte=RFC_A))
    p = unica(r)
    assert p.nivel == NIVEL_AUTOMATICO
    assert p.aplicaciones == ((1, D("1160.00")),)


def test_solo_el_importe_nunca_basta():
    r = MotorAsociacion([cfdi(1, "1160.00")]).proponer(mov(1, "1160.00"))
    assert r.propuestas == ()


def test_mismo_importe_con_otro_rfc_se_veta_y_se_explica():
    r = MotorAsociacion([cfdi(1, "1160.00", rfc=RFC_B)]).proponer(mov(1, "1160.00", rfc_contraparte=RFC_A))
    assert r.propuestas == ()
    assert r.descartados and r.descartados[0][0] == 1


def test_folio_sin_rfc_es_solo_sugerencia():
    r = MotorAsociacion([cfdi(1, "1160.00")]).proponer(mov(1, "1160.00", descripcion="PAGO FACT A1001"))
    p = unica(r)
    assert p.nivel == NIVEL_SUGERENCIA
    assert any("folio" in x.lower() for x in p.razones)


def test_folio_corto_no_cuenta_como_evidencia():
    corto = cfdi(1, "1160.00", serie="", folio="5")
    r = MotorAsociacion([corto]).proponer(mov(1, "1160.00", descripcion="TRANSFERENCIA 5"))
    assert r.propuestas == ()


def test_uuid_en_el_movimiento_es_identidad_fuerte():
    c = cfdi(1, "1160.00")
    texto = "PAGO " + c["uuid"].split("-")[-1]
    r = MotorAsociacion([c]).proponer(mov(1, "1160.00", descripcion=texto))
    assert unica(r).nivel == NIVEL_AUTOMATICO


def test_dos_facturas_iguales_de_la_misma_contraparte_son_ambiguas():
    motor = MotorAsociacion([cfdi(1, "5000.00"), cfdi(2, "5000.00")])
    r = motor.proponer(mov(1, "5000.00", rfc_contraparte=RFC_A))
    assert len(r.propuestas) == 2
    assert all(p.nivel == NIVEL_SUGERENCIA for p in r.propuestas)
    assert all(any("elegir" in a for a in p.advertencias) for p in r.propuestas)


def test_el_folio_desempata_entre_facturas_iguales():
    motor = MotorAsociacion([cfdi(1, "5000.00"), cfdi(2, "5000.00")])
    r = motor.proponer(mov(1, "5000.00", rfc_contraparte=RFC_A, descripcion="PAGO FACT A1002"))
    automaticas = [p for p in r.propuestas if p.nivel == NIVEL_AUTOMATICO]
    assert len(automaticas) == 1 and automaticas[0].aplicaciones[0][0] == 2


def test_direccion_incorrecta_no_asocia():
    r = MotorAsociacion([cfdi(1, "1160.00")]).proponer(mov(1, "1160.00", tipo="ingreso", rfc_contraparte=RFC_A))
    assert r.propuestas == ()


def test_ingreso_se_asocia_con_cfdi_emitido():
    r = MotorAsociacion([cfdi(1, "2320.00", tipo="emitido")]).proponer(
        mov(1, "2320.00", tipo="ingreso", rfc_contraparte=RFC_A))
    assert unica(r).nivel == NIVEL_AUTOMATICO


def test_nota_de_credito_recibida_se_asocia_con_un_ingreso():
    nota = cfdi(1, "580.00", tipo_comprobante="E")
    r = MotorAsociacion([nota]).proponer(mov(1, "580.00", tipo="ingreso", rfc_contraparte=RFC_A))
    assert unica(r).nivel == NIVEL_AUTOMATICO


def test_complemento_de_pago_y_nomina_se_excluyen():
    motor = MotorAsociacion([cfdi(1, "1160.00", tipo_comprobante="P"), cfdi(2, "1160.00", tipo_comprobante="N")])
    assert motor.proponer(mov(1, "1160.00", rfc_contraparte=RFC_A)).propuestas == ()


def test_cfdi_cancelado_o_ya_cubierto_se_ignora():
    motor = MotorAsociacion([cfdi(1, "1160.00", estatus="cancelado"), cfdi(2, "1160.00", saldo_pendiente=D("0"))])
    assert motor.proponer(mov(1, "1160.00", rfc_contraparte=RFC_A)).propuestas == ()


def test_se_compara_contra_el_saldo_pendiente_no_contra_el_total():
    motor = MotorAsociacion([cfdi(1, "1160.00", saldo_pendiente=D("580.00"))])
    p = unica(motor.proponer(mov(1, "580.00", rfc_contraparte=RFC_A)))
    assert p.nivel == NIVEL_AUTOMATICO


def test_pago_parcial_es_sugerencia_y_avisa_si_es_pue():
    motor = MotorAsociacion([cfdi(1, "1160.00", metodo_pago="PUE")])
    p = unica(motor.proponer(mov(1, "400.00", rfc_contraparte=RFC_A)))
    assert p.nivel == NIVEL_SUGERENCIA
    assert p.aplicaciones == ((1, D("400.00")),)
    assert any("PUE" in a for a in p.advertencias)


def test_un_pago_contra_varias_facturas_es_sugerencia():
    motor = MotorAsociacion([cfdi(1, "300.00"), cfdi(2, "700.00"), cfdi(3, "999.00")])
    r = motor.proponer(mov(1, "1000.00", rfc_contraparte=RFC_A))
    p = unica(r)
    assert p.nivel == NIVEL_SUGERENCIA
    assert sorted(i for i, _ in p.aplicaciones) == [1, 2]
    assert sum(i for _, i in p.aplicaciones) == D("1000.00")


def test_la_combinacion_no_mezcla_contrapartes():
    motor = MotorAsociacion([cfdi(1, "300.00"), cfdi(2, "700.00", rfc=RFC_B)])
    assert motor.proponer(mov(1, "1000.00", rfc_contraparte=RFC_A)).propuestas == ()


def test_cuenta_ligada_a_otro_proveedor_se_veta():
    motor = MotorAsociacion([cfdi(1, "1160.00", rfc=RFC_B)], cuentas_conocidas={RFC_A: ["012345678901234567"]})
    r = motor.proponer(mov(1, "1160.00", cuenta_bancaria_contraparte="012345678901234567"))
    assert r.propuestas == ()
    assert r.descartados


def test_cuenta_conocida_funciona_como_identidad_sin_rfc():
    motor = MotorAsociacion([cfdi(1, "1160.00")], cuentas_conocidas={RFC_A: ["012345678901234567"]})
    p = unica(motor.proponer(mov(1, "1160.00", cuenta_bancaria_contraparte="012 3456 7890 1234 567")))
    assert p.nivel == NIVEL_AUTOMATICO


def test_rfc_generico_de_publico_en_general_no_es_evidencia():
    publico = cfdi(1, "1160.00", rfc="XAXX010101000", tipo="emitido")
    r = MotorAsociacion([publico]).proponer(mov(1, "1160.00", tipo="ingreso", rfc_contraparte="XAXX010101000"))
    assert r.propuestas == ()


def test_factura_fechada_despues_del_pago_no_se_asocia_sola():
    tarde = cfdi(1, "1160.00", fecha="2026-10-20")
    p = unica(MotorAsociacion([tarde]).proponer(mov(1, "1160.00", rfc_contraparte=RFC_A)))
    assert p.nivel == NIVEL_SUGERENCIA
    assert any("después" in a for a in p.advertencias)


def test_moneda_extranjera_no_se_asocia_sola():
    usd = cfdi(1, "1160.00", moneda="USD")
    p = unica(MotorAsociacion([usd]).proponer(mov(1, "1160.00", rfc_contraparte=RFC_A)))
    assert p.nivel == NIVEL_SUGERENCIA


def test_dos_movimientos_que_reclaman_el_mismo_cfdi_se_degradan():
    motor = MotorAsociacion([cfdi(1, "1160.00")])
    resultados = motor.proponer_lote([
        mov(1, "1160.00", rfc_contraparte=RFC_A),
        mov(2, "1160.00", rfc_contraparte=RFC_A, fecha="2026-10-06"),
    ])
    for resultado in resultados.values():
        p = unica(resultado)
        assert p.nivel == NIVEL_SUGERENCIA
        assert any("también con los movimientos" in a for a in p.advertencias)


def test_un_lote_sin_conflicto_conserva_los_automaticos():
    motor = MotorAsociacion([cfdi(1, "1160.00"), cfdi(2, "580.00", rfc=RFC_B)])
    resultados = motor.proponer_lote([
        mov(1, "1160.00", rfc_contraparte=RFC_A),
        mov(2, "580.00", rfc_contraparte=RFC_B),
    ])
    assert all(unica(r).nivel == NIVEL_AUTOMATICO for r in resultados.values())


def test_rendimiento_con_miles_de_registros():
    cfdis = [cfdi(i, f"{100 + i}.00", rfc=f"RFC{i % 400:03d}010101AAA") for i in range(1, 4001)]
    movimientos = [mov(i, f"{100 + i}.00", rfc_contraparte=f"RFC{i % 400:03d}010101AAA") for i in range(1, 3001)]
    inicio = time.perf_counter()
    resultados = MotorAsociacion(cfdis).proponer_lote(movimientos)
    assert time.perf_counter() - inicio < 5
    assert sum(1 for r in resultados.values() if r.propuestas and r.propuestas[0].nivel == NIVEL_AUTOMATICO) == 3000
