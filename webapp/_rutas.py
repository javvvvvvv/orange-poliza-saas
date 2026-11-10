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
"""Agrega al sys.path las carpetas de backend que usan los módulos de webapp."""

import os
import sys

_BASE = os.path.join(os.path.dirname(__file__), "..", "backend", "app")
for _carpeta in ("core", "exporters"):
    _ruta = os.path.join(_BASE, _carpeta)
    if _ruta not in sys.path:
        sys.path.insert(0, _ruta)
