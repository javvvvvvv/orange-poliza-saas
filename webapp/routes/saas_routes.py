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

from flask import Blueprint, render_template, request, redirect, url_for, flash
from auth import login_required, superadmin_requerido
from db import get_connection
from werkzeug.security import generate_password_hash

saas_bp = Blueprint("saas", __name__)

@saas_bp.route("/saas", methods=["GET", "POST"])
@login_required
@superadmin_requerido
def saas_dashboard():
    con = get_connection()
    if request.method == "POST":
        nombre_org = request.form["nombre_org"].strip()
        nombre_admin = request.form["nombre_admin"].strip()
        usuario_admin = request.form["usuario_admin"].strip().lower()
        password_admin = request.form["password_admin"]
        
        # Validar
        existe = con.execute("SELECT id FROM usuarios WHERE usuario = ?", (usuario_admin,)).fetchone()
        if existe:
            flash(f"El usuario {usuario_admin} ya existe.", "error")
        else:
            cur = con.cursor()
            cur.execute("INSERT INTO organizaciones (nombre) VALUES (?)", (nombre_org,))
            org_id = cur.lastrowid
            cur.execute(
                "INSERT INTO usuarios (organizacion_id, nombre, usuario, password_hash, rol_global) VALUES (?, ?, ?, ?, 'admin')",
                (org_id, nombre_admin, usuario_admin, generate_password_hash(password_admin))
            )
            con.commit()
            flash(f"Despacho '{nombre_org}' creado exitosamente.", "exito")
            return redirect(url_for("saas.saas_dashboard"))
            
    organizaciones = con.execute("""
        SELECT o.id, o.nombre, o.creada_en, o.estado_suscripcion, o.fecha_vencimiento,
               (SELECT COUNT(*) FROM usuarios WHERE organizacion_id = o.id) as num_usuarios,
               (SELECT COUNT(*) FROM empresas WHERE organizacion_id = o.id) as num_empresas
        FROM organizaciones o
        ORDER BY o.id DESC
    """).fetchall()
    
    total_despachos = len(organizaciones)
    total_empresas = sum(o["num_empresas"] for o in organizaciones)
    total_polizas = con.execute("SELECT COUNT(*) FROM polizas").fetchone()[0] or 0
    
    con.close()
    
    return render_template("saas_dashboard.html", organizaciones=organizaciones,
                           total_despachos=total_despachos, total_empresas=total_empresas,
                           total_polizas=total_polizas)

@saas_bp.route("/saas/organizaciones/<int:org_id>/suspender", methods=["POST"])
@login_required
@superadmin_requerido
def suspender_org(org_id):
    con = get_connection()
    con.execute("UPDATE organizaciones SET estado_suscripcion = 'suspendida' WHERE id = ?", (org_id,))
    con.commit()
    con.close()
    flash("Despacho suspendido exitosamente.", "exito")
    return redirect(url_for("saas.saas_dashboard"))

@saas_bp.route("/saas/organizaciones/<int:org_id>/reactivar", methods=["POST"])
@login_required
@superadmin_requerido
def reactivar_org(org_id):
    con = get_connection()
    # Add 30 days
    import datetime
    nueva_fecha = (datetime.datetime.now() + datetime.timedelta(days=30)).isoformat()
    con.execute("UPDATE organizaciones SET estado_suscripcion = 'activa', fecha_vencimiento = ? WHERE id = ?", (nueva_fecha, org_id))
    con.commit()
    con.close()
    flash("Despacho reactivado por 30 días.", "exito")
    return redirect(url_for("saas.saas_dashboard"))

@saas_bp.route("/saas/organizaciones/<int:org_id>/renombrar", methods=["POST"])
@login_required
@superadmin_requerido
def renombrar_org(org_id):
    nuevo_nombre = request.form.get("nuevo_nombre", "").strip()
    if nuevo_nombre:
        con = get_connection()
        con.execute("UPDATE organizaciones SET nombre = ? WHERE id = ?", (nuevo_nombre, org_id))
        con.commit()
        con.close()
        flash("Despacho renombrado.", "exito")
    return redirect(url_for("saas.saas_dashboard"))

@saas_bp.route("/saas/organizaciones/<int:org_id>/eliminar", methods=["POST"])
@login_required
@superadmin_requerido
def eliminar_org(org_id):
    con = get_connection()
    # No eliminamos el id=1 (superadmin)
    if org_id == 1:
        flash("No puedes eliminar la organizacion principal.", "error")
    else:
        # Eliminacion en cascada simulada (o asumiendo que la BD tiene ON DELETE CASCADE)
        con.execute("DELETE FROM usuarios WHERE organizacion_id = ?", (org_id,))
        con.execute("DELETE FROM empresas WHERE organizacion_id = ?", (org_id,))
        con.execute("DELETE FROM organizaciones WHERE id = ?", (org_id,))
        con.commit()
        flash("Despacho eliminado completamente.", "exito")
    con.close()
    return redirect(url_for("saas.saas_dashboard"))

