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
import os
import datetime
try:
    import stripe
except ImportError:
    stripe = None
from db import get_connection

_PLACEHOLDERS_INVALIDOS = frozenset({
    "",
    "sk_test_mock_secret",
    "whsec_mock",
    "price_1XXXXXX",
    "TU_STRIPE_SECRET_KEY",
})

def stripe_configurado() -> bool:
    if stripe is None:
        return False
    sk = (os.environ.get("STRIPE_SECRET_KEY") or "").strip()
    price_id = (os.environ.get("STRIPE_PRICE_ID") or "").strip()
    return sk not in _PLACEHOLDERS_INVALIDOS and price_id not in _PLACEHOLDERS_INVALIDOS

def mock_billing_habilitado() -> bool:
    return (os.environ.get("ORANGE_ALLOW_MOCK_BILLING") or "").strip() == "1"

def get_checkout_url(org_id, success_url, cancel_url, host_url=""):
    """
    Retorna la URL a la que el usuario debe ser redirigido para pagar.
    Si Stripe está configurado con llaves reales, crea una sesión de Stripe Checkout.
    Solo permite el simulador local si ORANGE_ALLOW_MOCK_BILLING=1.
    """
    if stripe_configurado():
        stripe.api_key = os.environ.get("STRIPE_SECRET_KEY").strip()
        price_id = os.environ.get("STRIPE_PRICE_ID").strip()
        base = (host_url or "").rstrip("/")
        s_url = success_url if success_url.startswith("http") else f"{base}{success_url}"
        c_url = cancel_url if cancel_url.startswith("http") else f"{base}{cancel_url}"
        checkout_session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            success_url=s_url,
            cancel_url=c_url,
            client_reference_id=str(org_id),
        )
        return checkout_session.url

    if mock_billing_habilitado():
        return f"/billing/mock-checkout?org_id={int(org_id)}"

    raise RuntimeError(
        "La pasarela de pagos (Stripe) no está configurada en el servidor. "
        "Define STRIPE_SECRET_KEY y STRIPE_PRICE_ID en las variables de entorno."
    )

def reactivar_suscripcion(org_id, dias=30):
    """Reactiva la suscripción de la organización en la BD extendiendo desde hoy o desde el vencimiento vigente."""
    org_id_int = int(org_id)
    with get_connection() as con:
        fila = con.execute(
            "SELECT fecha_vencimiento FROM organizaciones WHERE id = ?",
            (org_id_int,),
        ).fetchone()
        ahora = datetime.datetime.now()
        base_dt = ahora
        if fila and fila["fecha_vencimiento"]:
            try:
                prev_dt = datetime.datetime.fromisoformat(str(fila["fecha_vencimiento"]))
                if prev_dt > ahora:
                    base_dt = prev_dt
            except ValueError:
                pass
        nueva_fecha = (base_dt + datetime.timedelta(days=int(dias))).isoformat()
        con.execute(
            "UPDATE organizaciones SET estado_suscripcion = 'activa', fecha_vencimiento = ? WHERE id = ?",
            (nueva_fecha, org_id_int),
        )
