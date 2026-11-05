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
from flask import Blueprint, redirect, render_template, request, url_for, g, flash
from werkzeug.exceptions import abort
from auth import login_required

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services"))
from billing_service import get_checkout_url, reactivar_suscripcion, mock_billing_habilitado

billing_bp = Blueprint("billing", __name__)

@billing_bp.route("/pagar")
@login_required
def pagar_suscripcion():
    if g.usuario.get("rol_global") not in ("superadmin", "admin", "gerente"):
        flash("Solo los administradores pueden gestionar la suscripción.", "error")
        return redirect(url_for("empresas.selector_empresas")), 403

    org_id = g.usuario["organizacion_id"]
    try:
        url = get_checkout_url(
            org_id,
            success_url=url_for("billing.pago_exitoso"),
            cancel_url=url_for("empresas.selector_empresas"),
            host_url=request.host_url,
        )
        return redirect(url, code=303)
    except Exception as e:
        flash(f"No se pudo iniciar el proceso de pago: {e}", "error")
        return redirect(url_for("empresas.selector_empresas"))

@billing_bp.route("/mock-checkout", methods=["GET", "POST"])
@login_required
def mock_checkout():
    es_superadmin = g.usuario.get("rol_global") == "superadmin"
    if not es_superadmin and not mock_billing_habilitado():
        abort(403, description="El simulador de pagos está deshabilitado en producción.")

    if g.usuario.get("rol_global") not in ("superadmin", "admin", "gerente"):
        abort(403, description="Solo los administradores del despacho pueden gestionar pagos.")

    org_id_param = request.args.get("org_id", type=int) or request.form.get("org_id", type=int)
    if es_superadmin and org_id_param:
        org_id = org_id_param
    else:
        if org_id_param and int(org_id_param) != int(g.usuario["organizacion_id"]):
            abort(403, description="No tienes permiso para gestionar la suscripción de otra organización.")
        org_id = g.usuario["organizacion_id"]

    if request.method == "POST":
        reactivar_suscripcion(org_id, dias=30)
        return redirect(url_for("billing.pago_exitoso"))

    return render_template("mock_checkout.html", org_id=org_id)

@billing_bp.route("/exito")
@login_required
def pago_exitoso():
    return render_template("pago_exitoso.html")
