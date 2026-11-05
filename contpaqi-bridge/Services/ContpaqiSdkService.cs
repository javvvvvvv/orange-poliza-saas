// ============================================================================
// PROPIEDAD INTELECTUAL Y LICENCIA COMERCIAL CERRADA
// ============================================================================
// Autor Legal y Titular de Derechos: JAVIER ILLAN GONZALEZ
// Organización: ORANGE CREW
// Contacto: ILLANJAVIER9@GMAIL.COM
//
// ADVERTENCIA LEGAL (MÉXICO Y GLOBAL):
// Este código fuente y su arquitectura son propiedad intelectual exclusiva de
// JAVIER ILLAN GONZALEZ. Queda estrictamente prohibida su reproducción,
// distribución, modificación, ingeniería inversa, copia o uso comercial sin la
// autorización expresa y por escrito del autor. Obra protegida conforme a la
// Ley Federal del Derecho de Autor y tratados internacionales aplicables.
// ============================================================================
using System.Runtime.InteropServices;
using Microsoft.CSharp.RuntimeBinder;
using ContpaqiBridge.Models;

namespace ContpaqiBridge.Services;

public sealed class ContpaqiSdkService
{
    private readonly StaComDispatcher _dispatcher;
    private readonly IConfiguration _config;
    private readonly ILogger<ContpaqiSdkService> _log;
    private dynamic? _sesion;
    private string? _empresaAbiertaActual;

    public ContpaqiSdkService(StaComDispatcher dispatcher, IConfiguration config, ILogger<ContpaqiSdkService> log)
    {
        _dispatcher = dispatcher;
        _config = config;
        _log = log;
    }

    public Task<bool> VerificarSdkDisponibleAsync() => _dispatcher.EjecutarAsync(() =>
    {
        try
        {
            var tipo = Type.GetTypeFromProgID("SDKCONTPAQNG.TSdkSesion");
            return tipo is not null;
        }
        catch
        {
            return false;
        }
    });

    private void AsegurarSesionIniciada()
    {
        if (_sesion is null)
        {
            var tipoSesion = Type.GetTypeFromProgID("SDKCONTPAQNG.TSdkSesion")
                ?? throw new InvalidOperationException("No se encontró SDKCONTPAQNG.TSdkSesion.");
            _sesion = Activator.CreateInstance(tipoSesion)!;
            _sesion.iniciaConexion();

            string usuario = _config["Contpaqi:Usuario"] ?? "";
            string contrasena = _config["Contpaqi:Contrasena"] ?? "";
            if (!string.IsNullOrEmpty(usuario))
            {
                _sesion.firmaUsuarioParams((string)usuario, (string)contrasena);
            }
            else
            {
                _sesion.firmaUsuario();
            }
            
            if (_sesion.ingresoUsuario == 0)
            {
                string error = _sesion.UltimoMsjError;
                throw new Exception($"Fallo al iniciar sesion en CONTPAQi: {error}. Verifica usuario y contrasena en appsettings.json");
            }
            
            _log.LogInformation("Sesión CONTPAQi iniciada.");
        }
    }

    private void AsegurarEmpresaAbierta(string nombreInternoEmpresa)
    {
        AsegurarSesionIniciada();

        if (_empresaAbiertaActual == nombreInternoEmpresa)
        {
            return;
        }

        if (_empresaAbiertaActual is not null)
        {
            _sesion!.cierraEmpresa();
        }

        var resultado = _sesion!.abreEmpresa(nombreInternoEmpresa);
        _log.LogInformation("abreEmpresa({Empresa}) -> {Resultado}", nombreInternoEmpresa, (object)resultado);
        _empresaAbiertaActual = nombreInternoEmpresa;
    }

    public Task<List<EmpresaDto>> ListarEmpresasAsync() => _dispatcher.EjecutarAsync(() =>
    {
        AsegurarSesionIniciada();
        
        _log.LogInformation("Instanciando TSdkListaEmpresas");
        var tipoListaEmpresas = Type.GetTypeFromProgID("SDKCONTPAQNG.TSdkListaEmpresas")
            ?? throw new InvalidOperationException("No se encontro SDKCONTPAQNG.TSdkListaEmpresas.");
        dynamic lista = Activator.CreateInstance(tipoListaEmpresas)!;

        var resultado = new List<EmpresaDto>();
        
        _log.LogInformation("Llamando buscaPrimero");
        int exito = lista.buscaPrimero();
        if (exito == 1)
        {
            do
            {
                string nombre = lista.Nombre;
                string baseDatos = lista.NombreBDD;
                resultado.Add(new EmpresaDto(nombre, baseDatos));
            } while (lista.buscaSiguiente() == 1);
        }
        return resultado;
    });

    public Task<RespuestaBridge> CrearCuentaAsync(string nombreInternoEmpresa, CuentaDto cuenta) => _dispatcher.EjecutarAsync(() =>
    {
        AsegurarEmpresaAbierta(nombreInternoEmpresa);
        var tipoCuenta = Type.GetTypeFromProgID("SDKCONTPAQNG.TSdkCuenta")!;
        dynamic cta = Activator.CreateInstance(tipoCuenta)!;
        cta.setSesion(_sesion);
        cta.iniciarInfo();
        cta.Codigo = cuenta.Codigo;
        cta.Nombre = cuenta.Nombre;
        if (!string.IsNullOrEmpty(cuenta.AgrupadorSat))
        {
            cta.AgrupadorSAT = cuenta.AgrupadorSat;
        }
        int res = 0;
        cta.crea(ref res);
        if (res != 1)
        {
            return new RespuestaBridge(false, $"No se pudo crear la cuenta: {cta.UltimoMsjError}");
        }
        return new RespuestaBridge(true, "Cuenta creada exitosamente");
    });

    public Task<List<CuentaDto>> ListarCuentasAsync(string nombreInternoEmpresa) => _dispatcher.EjecutarAsync(() =>
    {
        AsegurarEmpresaAbierta(nombreInternoEmpresa);

        var tipoCuenta = Type.GetTypeFromProgID("SDKCONTPAQNG.TSdkCuenta")!;
        dynamic cuenta = Activator.CreateInstance(tipoCuenta)!;
        cuenta.setSesion(_sesion);

        var resultado = new List<CuentaDto>();
        int exito = 0;
        string? ultimoCodigo = null;
        cuenta.consultaPorCodigo_buscaPrimero(ref exito);
        if (exito == 1)
        {
            do
            {
                try 
                {
                    string codigo = cuenta.Codigo;
                    if (string.IsNullOrEmpty(codigo) || codigo == ultimoCodigo)
                    {
                        break;
                    }
                    ultimoCodigo = codigo;
                    string nombre = cuenta.Nombre;
                    string? agrupador = null;
                    try { agrupador = cuenta.AgrupadorSAT; } catch (RuntimeBinderException) { /* opcional */ }
                    
                    if (codigo != "_CUADRE" && !codigo.StartsWith("_"))
                    {
                        resultado.Add(new CuentaDto(codigo, nombre, agrupador));
                    }
                }
                catch (Exception ex)
                {
                    _log.LogWarning($"Error leyendo cuenta: {ex.Message}");
                    break;
                }
                exito = 0;
                cuenta.consultaPorCodigo_buscaSiguiente(ref exito);
            } while (exito == 1);
        }
        return resultado;
    });

    public Task<RespuestaBridge> CrearPolizasAsync(string nombreInternoEmpresa, List<PolizaDto> polizas)
    {
        return _dispatcher.EjecutarAsync(() =>
        {
            try
            {
                AsegurarEmpresaAbierta(nombreInternoEmpresa);

                _log.LogInformation("Creando {N} pólizas en CONTPAQi...", polizas.Count);
                
                Type tipoPoliza = Type.GetTypeFromProgID("SDKCONTPAQNG.TSdkPoliza")!;
                Type tipoMov = Type.GetTypeFromProgID("SDKCONTPAQNG.TSdkMovimientoPoliza")!;
                
                int creadas = 0;

                foreach (var p in polizas)
                {
                    dynamic pol = Activator.CreateInstance(tipoPoliza)!;
                    pol.setSesion(_sesion);
                    
                    if (DateTime.TryParse(p.Fecha, out DateTime parsedDate))
                    {
                        pol.Fecha = parsedDate.ToString("MM/dd/yyyy");
                    }
                    else
                    {
                        pol.Fecha = p.Fecha;
                    }
                    
                    pol.Tipo = p.Tipo;
                    pol.Numero = int.Parse(p.Numero);
                    pol.Concepto = p.Concepto;
                    
                    int resCrea = pol.crea();
                    if (resCrea != 0)
                    {
                        _log.LogError("Error al crear encabezado de póliza {Fecha} {Concepto}: {Error}", p.Fecha, p.Concepto, resCrea);
                        continue;
                    }

                    foreach (var m in p.Movimientos)
                    {
                        dynamic mov = Activator.CreateInstance(tipoMov)!;
                        mov.setSesion(_sesion);
                        
                        mov.CodigoCuenta = m.Cuenta;
                        mov.TipoMovto = m.TipoMovto;
                        mov.Importe = (double)m.Importe;
                        mov.Concepto = string.IsNullOrEmpty(m.Concepto) ? p.Concepto : m.Concepto;
                        mov.Referencia = m.Referencia ?? "";
                        
                        int resMov = pol.creaMovimiento(mov);
                        if (resMov != 0)
                        {
                            _log.LogError("Error al crear movimiento a cuenta {Cuenta} por {Importe}: {Error}", m.Cuenta, m.Importe, resMov);
                        }
                    }
                    
                    // Asociar CFDIs
                    if (p.CfdiUuids != null && p.CfdiUuids.Count > 0)
                    {
                        try
                        {
                            Type tipoAsoc = Type.GetTypeFromProgID("SDKCONTPAQNG.TSdkAsocCFDI")!;
                            dynamic asoc = Activator.CreateInstance(tipoAsoc)!;
                            asoc.setSesion(_sesion);
                            
                            string guidPoliza = pol.Guid;
                            asoc.GuidDocumento = guidPoliza;
                            asoc.TipoAsoc = 1;
                            
                            foreach (var uuid in p.CfdiUuids)
                            {
                                if (!string.IsNullOrWhiteSpace(uuid))
                                {
                                    int resAsocAdd = asoc.agregaUUID(uuid);
                                    if (resAsocAdd != 0)
                                    {
                                        _log.LogWarning("Error al hacer agregaUUID({Uuid}): {Res}", uuid, resAsocAdd);
                                    }
                                }
                            }
                            
                            int resAsoc = asoc.crea();
                            if (resAsoc != 0)
                            {
                                _log.LogWarning("Error al finalizar asoc.crea() para la póliza: {Res}", resAsoc);
                            }
                            else
                            {
                                _log.LogInformation("CFDIs asociados exitosamente a la póliza {Guid}", guidPoliza);
                            }
                        }
                        catch (Exception exCFDI)
                        {
                            _log.LogError(exCFDI, "Excepción al intentar asociar CFDI a la póliza");
                        }
                    }

                    creadas++;
                }

                return new RespuestaBridge(true, "Se inyectaron " + creadas + " pólizas exitosamente en CONTPAQi.");
            }
            catch (Exception ex)
            {
                _log.LogError(ex, "Excepción en CrearPolizasAsync");
                return new RespuestaBridge(false, "Error desconocido: " + ex.Message);
            }
        });
    }
}
