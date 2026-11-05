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

try:
    from google import genai
    from google.genai import types
    HAS_GENAI = True
except ImportError:
    HAS_GENAI = False

def sugerir_cuenta_desde_orange(descripcion: str, tipo: str, monto: float) -> str:
    """
    Cerebro de 'Orange Intelligence'.
    """
    api_key = os.environ.get("GEMINI_API_KEY", "")
    
    if not HAS_GENAI or not api_key:
        desc_upper = descripcion.upper()
        if "UBER" in desc_upper or "TAXI" in desc_upper or "VUELO" in desc_upper:
            return "Gastos de Viaje"
        if "COMISION" in desc_upper or "SPEI" in desc_upper or "BANC" in desc_upper:
            return "Gastos Financieros"
        if "SEGURO" in desc_upper or "GMM" in desc_upper:
            return "Seguros y Fianzas"
        if "NOMINA" in desc_upper or "SUELDO" in desc_upper:
            return "Sueldos y Salarios"
        return "Gastos Generales"

    try:
        client = genai.Client(api_key=api_key)
        prompt = f"""
        Eres 'Orange Intelligence', el asistente contable mexicano experto.
        Dime sólo el nombre de la cuenta contable de ultimo nivel (gastos) ideal para este movimiento bancario:
        Descripción: {descripcion}
        Tipo: {tipo}
        Monto: {monto}
        Ejemplos: "Gastos de Viaje", "Gastos Financieros", "Seguros y Fianzas", "Papelería", "Honorarios".
        Responde sólo con el nombre de la cuenta, sin comillas ni explicaciones, maximo 3 palabras.
        """
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
        )
        return response.text.strip().title()
    except Exception:
        return "Gastos Generales"

