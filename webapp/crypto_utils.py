# ============================================================================
# PROPIEDAD INTELECTUAL Y LICENCIA COMERCIAL CERRADA
# ============================================================================
# Autor Legal y Titular de Derechos: JAVIER ILLAN GONZALEZ
# Organización: ORANGE CREW
# Contacto: ILLANJAVIER9@GMAIL.COM
# ============================================================================
"""
Módulo de utilidades criptográficas para cifrado simétrico autenticado (AEAD)
de credenciales sensibles en base de datos (ej. sql_password de CONTPAQi / SQL Server).
Implementación segura pura sin dependencias binarias externas (HMAC-SHA256 + CTR-stream).
"""
import base64
import hashlib
import hmac
import os
import secrets

_ENC_PREFIX = "ENC1:"

def _derivar_claves(master_secret: str) -> tuple[bytes, bytes]:
    """Deriva dos claves de 32 bytes (cifrado y autenticación) usando HKDF/SHA-256."""
    enc_key = hashlib.sha256((master_secret + ":ENC_KEY").encode("utf-8")).digest()
    auth_key = hashlib.sha256((master_secret + ":AUTH_KEY").encode("utf-8")).digest()
    return enc_key, auth_key

def _generar_keystream(enc_key: bytes, nonce: bytes, longitud: int) -> bytes:
    """Genera un keystream determinista seguro tipo CTR usando SHA-256."""
    keystream = bytearray()
    counter = 0
    while len(keystream) < longitud:
        bloque = hashlib.sha256(enc_key + nonce + counter.to_bytes(4, "big")).digest()
        keystream.extend(bloque)
        counter += 1
    return bytes(keystream[:longitud])

def cifrar_texto(texto_plano: str) -> str:
    """
    Cifra una cadena de texto en un token autenticado en Base64 seguro para URL/texto.
    Si el texto está vacío, regresa una cadena vacía.
    """
    if not texto_plano:
        return ""
    
    secret = os.environ.get("SECRET_KEY") or "orange_secret_key_default_production_2026"
    enc_key, auth_key = _derivar_claves(secret)
    
    datos = texto_plano.encode("utf-8")
    nonce = secrets.token_bytes(16)
    keystream = _generar_keystream(enc_key, nonce, len(datos))
    
    # XOR cifrado
    ciphertext = bytes(a ^ b for a, b in zip(datos, keystream))
    
    # Tag de autenticación HMAC sobre nonce + ciphertext
    tag = hmac.new(auth_key, nonce + ciphertext, hashlib.sha256).digest()
    
    payload = nonce + tag + ciphertext
    return _ENC_PREFIX + base64.urlsafe_b64encode(payload).decode("ascii")

def descifrar_texto(texto_cifrado: str) -> str:
    """
    Descifra un token generado por `cifrar_texto`.
    Si el texto no está cifrado (legacy texto plano), lo regresa tal cual para compatibilidad.
    Si la firma/HMAC es inválida, regresa cadena vacía para evitar ataques de manipulación.
    """
    if not texto_cifrado:
        return ""
    
    if not texto_cifrado.startswith(_ENC_PREFIX):
        # Compatibilidad con contraseñas históricas en texto plano
        return texto_cifrado
    
    secret = os.environ.get("SECRET_KEY") or "orange_secret_key_default_production_2026"
    enc_key, auth_key = _derivar_claves(secret)
    
    raw_b64 = texto_cifrado[len(_ENC_PREFIX):]
    try:
        payload = base64.urlsafe_b64decode(raw_b64.encode("ascii"))
    except Exception:
        return ""
    
    if len(payload) < 16 + 32:  # nonce (16) + tag (32) + al menos 0 bytes ciphertext
        return ""
    
    nonce = payload[:16]
    tag = payload[16:48]
    ciphertext = payload[48:]
    
    # Verificar HMAC de integridad y autenticidad en tiempo constante
    expected_tag = hmac.new(auth_key, nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected_tag):
        return ""
    
    keystream = _generar_keystream(enc_key, nonce, len(ciphertext))
    datos = bytes(a ^ b for a, b in zip(ciphertext, keystream))
    
    try:
        return datos.decode("utf-8")
    except UnicodeDecodeError:
        return ""
