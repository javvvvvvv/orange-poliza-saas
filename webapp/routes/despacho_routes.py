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
from flask import Blueprint, render_template, request, redirect, url_for, flash, g
from db import get_connection
from auth import login_required, gerente_requerido, log_audit, validar_rol_despacho_asignable
from werkzeug.security import generate_password_hash

despacho_bp = Blueprint("despacho", __name__)

@despacho_bp.route("/despacho/usuarios")
@login_required
@gerente_requerido
def listar_usuarios():
    with get_connection() as con:
        usuarios = con.execute("SELECT * FROM usuarios WHERE organizacion_id = ? ORDER BY id DESC", (g.usuario["organizacion_id"],)).fetchall()
        org_info = con.execute("SELECT limite_usuarios FROM organizaciones WHERE id = ?", (g.usuario["organizacion_id"],)).fetchone()
    return render_template("despacho_usuarios.html", usuarios=usuarios, limite=org_info["limite_usuarios"])

@despacho_bp.route("/despacho/usuarios/nuevo", methods=["POST"])
@login_required
@gerente_requerido
def nuevo_usuario():
    nombre = request.form.get("nombre")
    usuario = request.form.get("usuario")
    correo = request.form.get("correo")
    password = request.form.get("password")
    rol_raw = request.form.get("rol", "analista")
    
    if not all([nombre, usuario, correo]):
        flash("Nombre, Usuario y Correo son obligatorios.", "error")
        return redirect(url_for("despacho.listar_usuarios"))

    es_valido, resultado_rol = validar_rol_despacho_asignable(rol_raw, g.usuario.get("rol_global", "usuario"))
    if not es_valido:
        flash(resultado_rol, "error")
        return redirect(url_for("despacho.listar_usuarios")), 400
    rol = resultado_rol
        
    with get_connection() as con:
        # Revisar límite de usuarios
        org_info = con.execute("SELECT limite_usuarios FROM organizaciones WHERE id = ?", (g.usuario["organizacion_id"],)).fetchone()
        total_users = con.execute("SELECT COUNT(*) as count FROM usuarios WHERE organizacion_id = ?", (g.usuario["organizacion_id"],)).fetchone()
        
        if total_users and total_users["count"] >= org_info["limite_usuarios"]:
            flash(f"Has alcanzado el límite de tu suscripción ({org_info['limite_usuarios']} usuarios). Por favor, contacta a ventas para aumentar tu límite.", "error")
            return redirect(url_for("despacho.listar_usuarios"))
            
        existente = con.execute("SELECT id FROM usuarios WHERE usuario = ? OR correo = ?", (usuario, correo)).fetchone()

        if existente:
            flash("El nombre de usuario o correo ya está en uso.", "error")
            return redirect(url_for("despacho.listar_usuarios"))
            
        # Si no puso password, se queda en nulo (solo entra con SSO)
        hash_pwd = generate_password_hash(password) if password else None
            
        con.execute("INSERT INTO usuarios (organizacion_id, nombre, usuario, correo, password_hash, rol_global) VALUES (?, ?, ?, ?, ?, ?)",
                    (g.usuario["organizacion_id"], nombre, usuario, correo, hash_pwd, rol))
        con.commit()
        log_audit(g.usuario["organizacion_id"], g.usuario["id"], "Crear Usuario", f"Se invitó al usuario {usuario} ({correo})", con=con)
    
    # SIMULACIÓN DE ENVÍO DE CORREO (AQUÍ IRÍA SENDGRID O SMTP)
    print(f"--- ENVIANDO CORREO A {correo} ---")
    print(f"Hola {nombre}, has sido invitado a Orange Póliza.")
    print(f"Puedes entrar usando el botón de Iniciar Sesión con Google.")
    print(f"Link: {request.host_url}login")
    print("-----------------------------------")
    
    flash(f"Usuario {nombre} invitado con éxito. Se le ha enviado un correo.", "success")
    return redirect(url_for("despacho.listar_usuarios"))

@despacho_bp.route("/despacho/usuarios/<int:user_id>/eliminar", methods=["POST"])
@login_required
@gerente_requerido
def eliminar_usuario(user_id):
    if user_id == g.usuario["id"]:
        flash("No puedes eliminarte a ti mismo.", "error")
        return redirect(url_for("despacho.listar_usuarios"))
        
    with get_connection() as con:
        u = con.execute("SELECT usuario FROM usuarios WHERE id = ? AND organizacion_id = ?", (user_id, g.usuario["organizacion_id"])).fetchone()
        if u:
            con.execute("DELETE FROM usuarios WHERE id = ?", (user_id,))
            con.commit()
            log_audit(g.usuario["organizacion_id"], g.usuario["id"], "Eliminar Usuario", f"Se eliminó el usuario {u['usuario']}", con=con)
            flash("Usuario eliminado.", "exito")
        else:
            flash("Usuario no encontrado.", "error")
    return redirect(url_for("despacho.listar_usuarios"))

@despacho_bp.route("/despacho/bitacora")
@login_required
@gerente_requerido
def ver_bitacora():
    with get_connection() as con:
        logs = con.execute('''
            SELECT a.*, u.nombre as usuario_nombre
            FROM auditoria_despacho a
            LEFT JOIN usuarios u ON a.usuario_id = u.id
            WHERE a.organizacion_id = ?
            ORDER BY a.id DESC LIMIT 200
        ''', (g.usuario["organizacion_id"],)).fetchall()
    return render_template("despacho_bitacora.html", logs=logs)

