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
import sys
try:
    import stripe
except ImportError:
    stripe = None
from flask import Blueprint, redirect, request, jsonify, session, flash, url_for, render_template
from auth import login_required
from db import get_connection

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
from billing_service import get_checkout_url, reactivar_suscripcion

pagos_bp = Blueprint("pagos", __name__)

_WEBHOOK_PLACEHOLDERS = frozenset({"", "whsec_mock", "TU_STRIPE_WEBHOOK_SECRET"})

def _obtener_webhook_secret() -> str:
    sec = (os.environ.get("STRIPE_WEBHOOK_SECRET") or "").strip()
    return "" if sec in _WEBHOOK_PLACEHOLDERS else sec

@pagos_bp.route("/pagos/suscripcion-vencida")
@login_required
def suscripcion_vencida():
    return render_template("suscripcion_vencida.html"), 402

@pagos_bp.route("/pagos/checkout", methods=["GET", "POST"])
@login_required
def crear_checkout():
    usuario_id = session.get("usuario_id")
    if not usuario_id:
        return redirect(url_for("auth.login"))

    with get_connection() as con:
        usr = con.execute(
            "SELECT organizacion_id, rol_global FROM usuarios WHERE id = ?",
            (usuario_id,),
        ).fetchone()

    if not usr or usr["rol_global"] not in ("superadmin", "admin", "gerente"):
        flash("Solo los administradores pueden gestionar la suscripción.", "error")
        return redirect(url_for("empresas.selector_empresas"))

    org_id = usr["organizacion_id"]
    domain_url = request.host_url.rstrip("/")

    try:
        url = get_checkout_url(
            org_id,
            success_url=domain_url + url_for("pagos.exito") + "?session_id={CHECKOUT_SESSION_ID}",
            cancel_url=domain_url + url_for("empresas.selector_empresas"),
            host_url=domain_url,
        )
        return redirect(url, code=303)
    except Exception as e:
        flash(f"Error al conectar con la pasarela de pagos: {str(e)}", "error")
        return redirect(url_for("empresas.selector_empresas"))

@pagos_bp.route("/pagos/exito")
@login_required
def exito():
    flash("¡Pago completado! Tu suscripción ha sido procesada correctamente.", "exito")
    return redirect(url_for("empresas.selector_empresas"))

@pagos_bp.route("/pagos/webhook", methods=["POST"])
def stripe_webhook():
    webhook_secret = _obtener_webhook_secret()
    if not webhook_secret:
        return jsonify({"exito": False, "mensaje": "STRIPE_WEBHOOK_SECRET no configurado."}), 503

    payload = request.get_data(as_text=True)
    sig_header = request.headers.get("Stripe-Signature")
    if not sig_header:
        return jsonify({"exito": False, "mensaje": "Falta cabecera Stripe-Signature."}), 400

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except Exception:
        return jsonify({"exito": False, "mensaje": "Firma de webhook inválida."}), 400

    if event.get("type") == "checkout.session.completed":
        session_obj = event.get("data", {}).get("object", {})
        org_id = session_obj.get("client_reference_id")
        if org_id and str(org_id).isdigit():
            _acreditar_pago(int(org_id))

    return jsonify(success=True), 200

def _acreditar_pago(org_id):
    reactivar_suscripcion(org_id, dias=30)
