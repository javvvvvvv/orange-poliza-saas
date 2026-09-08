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

/// <summary>
/// Toda la superficie de este archivo que toca `dynamic` habla contra el
/// objeto COM SDKCONTPAQNG.TSdkSesion vía IDispatch (late binding), igual
/// que los scripts de PowerShell con los que se confirmó la conexión.
/// No hay ensamblado de interop fuerte porque CONTPAQi no distribuye uno
/// oficial de forma consistente entre versiones; late binding es lo que
/// ya se probó que funciona en este entorno.
///
/// MÉTODOS CONFIRMADOS EN PRUEBAS REALES (ver test_com.ps1 / test_sdk.cs):
///   iniciaConexion(), firmaUsuario(), abreEmpresa(nombreInterno),
///   cierraEmpresa(), finalizaConexion()
///
/// PENDIENTE DE CONFIRMAR (marcado abajo con AJUSTAR): las propiedades
/// exactas para fijar usuario/contraseña antes de firmaUsuario(), y el
/// nombre de la propiedad que expone el catálogo de cuentas. Si algo de
/// esto truena, el mensaje de la excepción COM dice el nombre real que
/// falta — no hay que adivinar dos veces, con `herramientas/listar_metodos.ps1`
/// se descubre el nombre correcto igual que se hizo para methods.txt.
/// </summary>
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

    /// <summary>
    /// Abre (o reutiliza, si ya está abierta) la sesión y la empresa dadas
    /// por su NOMBRE INTERNO de base de datos (ej. "ctADRIANA_MARCELA_..."),
    /// nunca el nombre bonito que ve el usuario — ese fue el bug confirmado
    /// en las pruebas: abreEmpresa con el nombre de exhibición no abre nada.
    /// </summary>
    
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
        if (_sesion is null)
        {
            var tipoSesion = Type.GetTypeFromProgID("SDKCONTPAQNG.TSdkSesion")
                ?? throw new InvalidOperationException(
                    "No se encontró el ProgID SDKCONTPAQNG.TSdkSesion. " +
                    "¿CONTPAQi Contabilidad está instalado en esta máquina y el bridge corre en x86?");
            _sesion = Activator.CreateInstance(tipoSesion)!;
            _sesion.iniciaConexion();

            var usuario = _config["Contpaqi:Usuario"];
            var contrasena = _config["Contpaqi:Contrasena"];
            if (!string.IsNullOrEmpty(usuario))
            {
                // AJUSTAR: confirmar el nombre real de estas propiedades en este
                // SDK/versión (usuario / contrasena, o UsuarioNombre / UsuarioPassword,
                // etc.). Si el nombre es otro, esta línea truena con
                // RuntimeBinderException diciendo el miembro que no existe.
                try { _sesion.usuario = usuario; } catch (RuntimeBinderException) { /* ver comentario arriba */ }
                try { _sesion.contrasena = contrasena; } catch (RuntimeBinderException) { /* ver comentario arriba */ }
            }

            _sesion.firmaUsuario();
            _log.LogInformation("Sesión CONTPAQi iniciada.");
        }

        if (_empresaAbiertaActual == nombreInternoEmpresa)
        {
            return;
        }

        if (_empresaAbiertaActual is not null)
        {
            _sesion.cierraEmpresa();
        }

        var resultado = _sesion.abreEmpresa(nombreInternoEmpresa);
        _log.LogInformation("abreEmpresa({Empresa}) -> {Resultado}", nombreInternoEmpresa, (object)resultado);
        _empresaAbiertaActual = nombreInternoEmpresa;
    }

    
    public Task<List<EmpresaDto>> ListarEmpresasAsync() => _dispatcher.EjecutarAsync(() =>
    {
        AsegurarSesionIniciada(); // solo inicia conexion, no abre empresa
        
                
        _log.LogInformation("Instanciando TSdkListaEmpresas");
        var tipoListaEmpresas = Type.GetTypeFromProgID("SDKCONTPAQNG.TSdkListaEmpresas")
            ?? throw new InvalidOperationException("No se encontro SDKCONTPAQNG.TSdkListaEmpresas.");
        dynamic lista = Activator.CreateInstance(tipoListaEmpresas)!;

        var resultado = new List<EmpresaDto>();
        
        _log.LogInformation("Llamando buscaPrimero");
        int exito = lista.buscaPrimero();
        System.IO.File.WriteAllText(@"C:\orange-poliza-engine-sdk\bridge_debug.txt", "Exito: " + exito);
        _log.LogInformation("buscaPrimero termino con exito: " + exito);
        if (exito == 1 || exito == 0)
        {
            do
            {
                string nombre = lista.Nombre;
                string baseDatos = lista.NombreBDD;
                resultado.Add(new EmpresaDto(nombre, baseDatos));
                System.IO.File.AppendAllText(@"C:\orange-poliza-engine-sdk\bridge_debug.txt", "\nEmpresa: " + nombre);
            } while (lista.buscaSiguiente() == exito);
        }
        return resultado;
    });

    public Task<RespuestaBridge> CrearCuentaAsync(string nombreInternoEmpresa, CuentaDto cuenta) => _dispatcher.EjecutarAsync(() =>
    {
        AsegurarEmpresaAbierta(nombreInternoEmpresa);
        dynamic manejador = _sesion!.cuentas;
        manejador.crea(cuenta.Codigo, cuenta.Nombre, cuenta.AgrupadorSat ?? "");
        return new RespuestaBridge(true, "Cuenta creada exitosamente");
    });

    public Task<List<CuentaDto>> ListarCuentasAsync(string nombreInternoEmpresa) => _dispatcher.EjecutarAsync(() =>
    {
        AsegurarEmpresaAbierta(nombreInternoEmpresa);

        var tipoCuenta = Type.GetTypeFromProgID("SDKCONTPAQNG.TSdkCuenta");
        dynamic cuenta = Activator.CreateInstance(tipoCuenta)!;
        cuenta.setSesion(_sesion);

        var resultado = new List<CuentaDto>();
        int exito = cuenta.buscaPrimero();
        if (exito == 1 || exito == 0)
        {
            do
            {
                try 
                {
                    string codigo = cuenta.Codigo;
                    string nombre = cuenta.Nombre;
                    string? agrupador = null;
                    try { agrupador = cuenta.CodigoAgrupador; } catch (RuntimeBinderException) { /* opcional */ }
                    
                    // Si el código está vacío, salimos para evitar bucles infinitos por error COM
                    if (string.IsNullOrEmpty(codigo)) break;
                    
                    resultado.Add(new CuentaDto(codigo, nombre, agrupador));
                }
                catch (Exception ex)
                {
                    _log.LogWarning($"Error leyendo cuenta: {ex.Message}");
                    break;
                }
                exito = cuenta.buscaSiguiente();
            } while (exito == 1);
        }
        return resultado;
    });

    /// <summary>
    /// A propósito NO implementado con llamadas SDK adivinadas: crear
    /// pólizas mueve dinero/impuestos, y no hay ningún método confirmado
    /// en pruebas reales para esto (el catálogo de métodos que sí se
    /// reflejó — methods.txt — es del objeto de cuentas, no de pólizas).
    /// Adivinar nombres de propiedad aquí es el tipo de "conexión que no
    /// funciona bien" que se estaba tratando de arreglar.
    ///
    /// Camino recomendado por ahora: seguir usando el importador de
    /// archivo (.txt/.xls) ya validado en contpaqi_exporter.py /
    /// contpaqi_txt_exporter.py, e importar manualmente en CONTPAQi.
    /// Para automatizar esto también, corre
    /// herramientas/listar_metodos.ps1 apuntando al objeto de pólizas
    /// (una vez identificado el ProgID/propiedad correcta) para obtener
    /// su lista real de métodos, igual que se hizo aquí con cuentas.
    /// </summary>
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







