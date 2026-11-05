# ============================================================================
#    PROPIEDAD INTELECTUAL Y LICENCIA COMERCIAL CERRADA
#    ============================================================================
#    Autor Legal y Titular de Derechos: JAVIER ILLAN GONZALEZ
#    Organización: ORANGE CREW
#    Contacto: ILLANJAVIER9@GMAIL.COM
#
#    ADVERTENCIA LEGAL (MÉXICO Y GLOBAL):
#    Este código fuente y su arquitectura son propiedad intelectual exclusiva de
#    JAVIER ILLAN GONZALEZ. Queda estrictamente prohibida su reproducción,
#    distribución, modificación, ingeniería inversa, copia o uso comercial sin la
#    autorización expresa y por escrito del autor. Obra protegida conforme a la
#    Ley Federal del Derecho de Autor y tratados internacionales aplicables.
#    ============================================================================
"""
Restablece la contraseña de un usuario directo en PostgreSQL, sin pasar
por el login (para cuando alguien se quedó fuera de su cuenta). Se
invoca desde resetear_password.bat; también se puede correr a mano:

    python resetear_password.py <usuario> <password_nueva>
"""

import sys

from werkzeug.security import generate_password_hash

from db import ErrorConexionBD, get_connection

def resetear_password(usuario: str, password_nueva: str) -> bool:
    """Regresa True si encontró y actualizó al usuario, False si no existe."""
    usuario_norm = usuario.strip().lower()
    con = get_connection()
    try:
        fila = con.execute(
            "SELECT id FROM usuarios WHERE usuario = ?", (usuario_norm,)
        ).fetchone()
        if fila is None:
            return False
        con.execute(
            "UPDATE usuarios SET password_hash = ? WHERE id = ?",
            (generate_password_hash(password_nueva), fila["id"]),
        )
        con.commit()
        return True
    finally:
        con.close()

def main():
    if len(sys.argv) != 3:
        print("Uso: python resetear_password.py <usuario> <password_nueva>")
        sys.exit(1)

    usuario, password_nueva = sys.argv[1], sys.argv[2]
    if len(password_nueva) < 4:
        print("La contraseña nueva debe tener al menos 4 caracteres.")
        sys.exit(1)

    try:
        encontrado = resetear_password(usuario, password_nueva)
    except ErrorConexionBD as e:
        print(f"Error de conexión: {e}")
        sys.exit(1)

    if not encontrado:
        print(f'No existe ningún usuario con el nombre de usuario "{usuario}".')
        sys.exit(1)

    print(f'Contraseña de "{usuario}" actualizada correctamente.')

if __name__ == "__main__":
    main()
