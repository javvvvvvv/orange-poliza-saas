namespace ContpaqiBridge.Models;

public record MovimientoDto(
    string Cuenta,
    int TipoMovto,
    decimal Importe,
    string? Concepto,
    string? Referencia
);

public record PolizaDto(
    string Numero,
    int Tipo,
    string Fecha,
    string Concepto,
    List<MovimientoDto> Movimientos,
    List<string>? CfdiUuids = null
);

public record ExportarPolizasRequest(string Empresa, List<PolizaDto> Polizas);

public record CuentasRequest(string Empresa, bool Forzar = false, int TotalEstimado = 0);

public record MetaCatalogoDto(int UltimoId, DateTime UltimaSincronizacionUtc, int TotalCuentas);

public record ResultadoCuentasDto(List<CuentaDto> Cuentas, int Nuevas, string Modo);

public record ProgresoCuentasDto(
    string Empresa,
    bool EnCurso,
    bool Terminado,
    int Leidas,
    int TotalEstimado,
    double Porcentaje,
    double Segundos,
    double CuentasPorSegundo,
    string Etapa,
    string? UltimaCuenta,
    bool DesdeCache,
    string? Error,
    int Nuevas = 0,
    string Modo = "incremental"
);

public record RespuestaBridge(bool Exito, string Mensaje, object? Datos = null);

public record CuentaDto(
    string Codigo,
    string Nombre,
    string? AgrupadorSat,
    int IdContpaqi = 0,
    string? IdComparacion = null
)
{
    public string ObtenerIdComparacion() =>
        !string.IsNullOrWhiteSpace(IdComparacion)
            ? IdComparacion!
            : $"{IdContpaqi}|{Codigo.Trim()}|{Nombre.Trim()}";
}

public record EmpresaDto(string Nombre, string BaseDatos);
public record CrearCuentaRequest(string Empresa, CuentaDto Cuenta);
