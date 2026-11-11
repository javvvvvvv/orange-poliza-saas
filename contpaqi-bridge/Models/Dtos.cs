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
    string? Error
);

public record RespuestaBridge(bool Exito, string Mensaje, object? Datos = null);

public record CuentaDto(string Codigo, string Nombre, string? AgrupadorSat);

public record EmpresaDto(string Nombre, string BaseDatos);
public record CrearCuentaRequest(string Empresa, CuentaDto Cuenta);
