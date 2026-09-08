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

public record CuentasRequest(string Empresa);

public record RespuestaBridge(bool Exito, string Mensaje, object? Datos = null);

public record CuentaDto(string Codigo, string Nombre, string? AgrupadorSat);

public record EmpresaDto(string Nombre, string BaseDatos);
public record CrearCuentaRequest(string Empresa, CuentaDto Cuenta);
