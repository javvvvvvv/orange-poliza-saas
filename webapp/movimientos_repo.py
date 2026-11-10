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

"""Fachada de compatibilidad: reexporta lo que antes vivía en este archivo.

Cada responsabilidad vive ahora en su propio módulo (movimientos_importacion,
movimientos_clasificacion, polizas_generacion, polizas_exportacion, polizas_edicion).
Las rutas pueden seguir importando desde `movimientos_repo`."""
import _rutas  # noqa: F401
from db import get_connection  # noqa: F401
from licencia import LicenciaInvalida  # noqa: F401
from movimientos_comun import (  # noqa: F401
    EmpresaNoEncontradaError,
    TotalPolizasGeneradas,
    _fila_a_movimiento_dict,
    _construir_info_descuadre,
    _aplicar_impuestos_cfdi_a_movimiento,
)
from movimientos_importacion import (  # noqa: F401
    IdsInsertados,
    insertar_movimientos,
)
from movimientos_clasificacion import (  # noqa: F401
    registrar_memoria_ml_exitosa,
    entrenar_modelo_empresa,
    clasificar_pendientes,
    listar_movimientos,
    sugerencia_para_movimiento,
)
from polizas_generacion import (  # noqa: F401
    generar_polizas_pendientes,
)
from polizas_exportacion import (  # noqa: F401
    SinPolizasPorExportarError,
    BancoSinCuentaContableError,
    exportar_polizas,
)
from polizas_edicion import (  # noqa: F401
    PolizasYaEnviadasError,
    actualizar_poliza,
    limpiar_polizas,
    limpiar_movimientos,
    _limpiar_polizas_en_conexion,
)
