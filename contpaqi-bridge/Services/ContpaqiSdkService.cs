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

using System.Collections.Concurrent;

using System.Diagnostics;

using System.Runtime.InteropServices;

using System.Text.Json;

using Microsoft.CSharp.RuntimeBinder;

using ContpaqiBridge.Models;



namespace ContpaqiBridge.Services;



public sealed class ContpaqiSdkService : IDisposable

{

    private readonly StaComDispatcher _dispatcher;

    private readonly IConfiguration _config;

    private readonly ILogger<ContpaqiSdkService> _log;

    private readonly string _cacheDir;

    private dynamic? _sesion;

    private string? _empresaAbiertaActual;



    private List<EmpresaDto>? _cacheEmpresas;

    private DateTime _cacheEmpresasExpira = DateTime.MinValue;

    private readonly ConcurrentDictionary<string, (List<CuentaDto> Cuentas, DateTime Expira)> _cacheCuentas = new(StringComparer.OrdinalIgnoreCase);

    private readonly ConcurrentDictionary<string, Task<ResultadoCuentasDto>> _enVueloCuentas = new(StringComparer.OrdinalIgnoreCase);

    private readonly ConcurrentDictionary<string, ProgresoCuentasDto> _progresoCuentas = new(StringComparer.OrdinalIgnoreCase);



    public ContpaqiSdkService(StaComDispatcher dispatcher, IConfiguration config, ILogger<ContpaqiSdkService> log)

    {

        _dispatcher = dispatcher;

        _config = config;

        _log = log;

        _cacheDir = Path.Combine(Directory.GetCurrentDirectory(), "cache");

        try

        {

            Directory.CreateDirectory(_cacheDir);

        }

        catch (Exception ex)

        {

            _log.LogWarning(ex, "No se pudo crear directorio de caché en {Dir}", _cacheDir);

        }

    }



    private string LimpiarNombreArchivo(string nombreInternoEmpresa) =>

        string.Concat(nombreInternoEmpresa.Select(c => Path.GetInvalidFileNameChars().Contains(c) ? '_' : c));



    private string ObtenerRutaCacheCuentas(string nombreInternoEmpresa) =>

        Path.Combine(_cacheDir, $"cuentas_{LimpiarNombreArchivo(nombreInternoEmpresa)}.json");



    private string ObtenerRutaMetaCuentas(string nombreInternoEmpresa) =>

        Path.Combine(_cacheDir, $"meta_{LimpiarNombreArchivo(nombreInternoEmpresa)}.json");



    private List<CuentaDto>? IntentarLeerCacheDisco(string nombreInternoEmpresa, TimeSpan maxEdad)

    {

        try

        {

            var ruta = ObtenerRutaCacheCuentas(nombreInternoEmpresa);

            if (!File.Exists(ruta)) return null;

            var info = new FileInfo(ruta);

            if (DateTime.UtcNow - info.LastWriteTimeUtc > maxEdad) return null;

            var json = File.ReadAllText(ruta);

            var lista = JsonSerializer.Deserialize<List<CuentaDto>>(json);

            if (lista is not null && lista.Count > 0)

            {

                return lista;

            }

        }

        catch (Exception ex)

        {

            _log.LogWarning(ex, "Aviso al leer caché en disco para {Empresa}", nombreInternoEmpresa);

        }

        return null;

    }



    private void GuardarCacheDisco(string nombreInternoEmpresa, List<CuentaDto> cuentas)

    {

        try

        {

            Directory.CreateDirectory(_cacheDir);

            var ruta = ObtenerRutaCacheCuentas(nombreInternoEmpresa);

            var json = JsonSerializer.Serialize(cuentas);

            File.WriteAllText(ruta, json);

        }

        catch (Exception ex)

        {

            _log.LogWarning(ex, "Aviso al guardar caché en disco para {Empresa}", nombreInternoEmpresa);

        }

    }



    private MetaCatalogoDto? IntentarLeerMetaDisco(string nombreInternoEmpresa)

    {

        try

        {

            var ruta = ObtenerRutaMetaCuentas(nombreInternoEmpresa);

            if (!File.Exists(ruta)) return null;

            var json = File.ReadAllText(ruta);

            return JsonSerializer.Deserialize<MetaCatalogoDto>(json);

        }

        catch

        {

            return null;

        }

    }



    private void GuardarMetaDisco(string nombreInternoEmpresa, int ultimoId, int totalCuentas)

    {

        try

        {

            Directory.CreateDirectory(_cacheDir);

            var ruta = ObtenerRutaMetaCuentas(nombreInternoEmpresa);

            var meta = new MetaCatalogoDto(ultimoId, DateTime.UtcNow, totalCuentas);

            File.WriteAllText(ruta, JsonSerializer.Serialize(meta));

        }

        catch (Exception ex)

        {

            _log.LogWarning(ex, "Aviso al guardar metadatos de catálogo para {Empresa}", nombreInternoEmpresa);

        }

    }



    private static bool BuscarCuentaPorId(dynamic cuenta, int id)

    {

        try

        {

            int r = cuenta.buscaPorId(id);

            return r == 1;

        }

        catch

        {

            try

            {

                int exito = 0;

                cuenta.buscaPorId(id, ref exito);

                return exito == 1;

            }

            catch

            {

                return false;

            }

        }

    }



    private static int ObtenerIdCuenta(dynamic cuenta)

    {

        try

        {

            return (int)cuenta.Id;

        }

        catch

        {

            return 0;

        }

    }



    private static int BuscarIdEnVentana(dynamic cuenta, int idInicio, int ventana = 6)

    {

        int mayorEncontrado = 0;

        for (int offset = 0; offset < ventana; offset++)

        {

            int candidato = idInicio + offset;

            if (candidato >= 1 && BuscarCuentaPorId(cuenta, candidato))

            {

                mayorEncontrado = candidato;

            }

        }

        return mayorEncontrado;

    }



    public ProgresoCuentasDto ObtenerProgresoCuentas(string nombreInternoEmpresa)

    {

        if (_progresoCuentas.TryGetValue(nombreInternoEmpresa, out var prog))

        {

            return prog;

        }

        if (_cacheCuentas.TryGetValue(nombreInternoEmpresa, out var enMem) && enMem.Cuentas.Count > 0)

        {

            return new ProgresoCuentasDto(

                nombreInternoEmpresa,

                EnCurso: false,

                Terminado: true,

                Leidas: enMem.Cuentas.Count,

                TotalEstimado: enMem.Cuentas.Count,

                Porcentaje: 100.0,

                Segundos: 0.1,

                CuentasPorSegundo: enMem.Cuentas.Count,

                Etapa: "Catálogo sincronizado",

                UltimaCuenta: null,

                DesdeCache: true,

                Error: null,

                Nuevas: 0,

                Modo: "incremental"

            );

        }

        return new ProgresoCuentasDto(

            nombreInternoEmpresa,

            EnCurso: false,

            Terminado: false,

            Leidas: 0,

            TotalEstimado: 0,

            Porcentaje: 0.0,

            Segundos: 0.0,

            CuentasPorSegundo: 0.0,

            Etapa: "En espera",

            UltimaCuenta: null,

            DesdeCache: false,

            Error: null,

            Nuevas: 0,

            Modo: "incremental"

        );

    }



    public Task<bool> VerificarSdkDisponibleAsync()

    {

        try

        {

            var tipo = Type.GetTypeFromProgID("SDKCONTPAQNG.TSdkSesion");

            return Task.FromResult(tipo is not null);

        }

        catch

        {

            return Task.FromResult(false);

        }

    }



    private void AsegurarSesionIniciada()

    {

        if (_sesion is null)

        {

            var tipoSesion = Type.GetTypeFromProgID("SDKCONTPAQNG.TSdkSesion")

                ?? throw new InvalidOperationException("No se encontró SDKCONTPAQNG.TSdkSesion.");

            _sesion = Activator.CreateInstance(tipoSesion)!;

            _log.LogInformation("Llamando iniciaConexion()...");

            _sesion.iniciaConexion();



            string usuario = _config["Contpaqi:Usuario"] ?? "";

            string contrasena = _config["Contpaqi:Contrasena"] ?? "";

            _log.LogInformation("Llamando firmaUsuarioParams({Usuario})...", usuario);

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

            try

            {

                _sesion!.cierraEmpresa();

            }

            catch (Exception ex)

            {

                _log.LogWarning(ex, "Aviso al cerrar empresa previa {Empresa}", _empresaAbiertaActual);

            }

            _empresaAbiertaActual = null;

        }



        var resultado = _sesion!.abreEmpresa(nombreInternoEmpresa);

        _log.LogInformation("abreEmpresa({Empresa}) -> {Resultado}", nombreInternoEmpresa, (object)resultado);

        if (resultado != 1)

        {

            _empresaAbiertaActual = null;

            string msj = "";

            try { msj = _sesion!.UltimoMsjError; } catch { }

            throw new InvalidOperationException($"No se pudo abrir la empresa '{nombreInternoEmpresa}' en CONTPAQi (Código: {resultado}). {msj}");

        }



        _empresaAbiertaActual = nombreInternoEmpresa;

    }



    public Task<List<EmpresaDto>> ListarEmpresasAsync()

    {

        if (_cacheEmpresas is not null && DateTime.UtcNow < _cacheEmpresasExpira && _cacheEmpresas.Count > 0)

        {

            return Task.FromResult(_cacheEmpresas);

        }



        return _dispatcher.EjecutarAsync(() =>

        {

            if (_cacheEmpresas is not null && DateTime.UtcNow < _cacheEmpresasExpira && _cacheEmpresas.Count > 0)

            {

                return _cacheEmpresas;

            }



            AsegurarSesionIniciada();

            

            _log.LogInformation("Instanciando TSdkListaEmpresas");

            var tipoListaEmpresas = Type.GetTypeFromProgID("SDKCONTPAQNG.TSdkListaEmpresas")

                ?? throw new InvalidOperationException("No se encontro SDKCONTPAQNG.TSdkListaEmpresas.");

            dynamic lista = Activator.CreateInstance(tipoListaEmpresas)!;



            try

            {

                var resultado = new List<EmpresaDto>();

                

                _log.LogInformation("Llamando buscaPrimero");

                int exito = lista.buscaPrimero();

                if (exito == 1)

                {

                    do

                    {

                        string nombre = ((string)lista.Nombre).Replace("\0", "").Trim();

                        string baseDatos = ((string)lista.NombreBDD).Replace("\0", "").Trim();

                        resultado.Add(new EmpresaDto(nombre, baseDatos));

                    } while (lista.buscaSiguiente() == 1);

                }

                if (resultado.Count > 0)

                {

                    _cacheEmpresas = resultado;

                    _cacheEmpresasExpira = DateTime.UtcNow.AddMinutes(30);

                }

                return resultado;

            }

            finally

            {

                try { Marshal.ReleaseComObject(lista); } catch { }

            }

        });

    }



    public Task<RespuestaBridge> CrearCuentaAsync(string nombreInternoEmpresa, CuentaDto cuenta) => _dispatcher.EjecutarAsync(() =>

    {

        AsegurarEmpresaAbierta(nombreInternoEmpresa);

        var tipoCuenta = Type.GetTypeFromProgID("SDKCONTPAQNG.TSdkCuenta")!;

        dynamic cta = Activator.CreateInstance(tipoCuenta)!;

        try

        {

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

            if (_cacheCuentas.TryGetValue(nombreInternoEmpresa, out var actual))

            {

                actual.Cuentas.Add(cuenta);

                GuardarCacheDisco(nombreInternoEmpresa, actual.Cuentas);

            }

            else

            {

                _cacheCuentas.TryRemove(nombreInternoEmpresa, out _);

            }

            return new RespuestaBridge(true, "Cuenta creada exitosamente");

        }

        finally

        {

            try { Marshal.ReleaseComObject(cta); } catch { }

        }

    });



    public async Task<ResultadoCuentasDto> ListarCuentasConDetalleAsync(string nombreInternoEmpresa, bool forzar = false, int totalEstimado = 0)

    {

        // Cargar base existente (de memoria o disco) para poder hacer sincronización incremental

        List<CuentaDto>? baseExistente = null;

        if (_cacheCuentas.TryGetValue(nombreInternoEmpresa, out var enMem) && enMem.Cuentas.Count > 0)

        {

            baseExistente = enMem.Cuentas;

        }

        else

        {

            baseExistente = IntentarLeerCacheDisco(nombreInternoEmpresa, TimeSpan.FromDays(365));

            if (baseExistente is not null && baseExistente.Count > 0)

            {

                _cacheCuentas[nombreInternoEmpresa] = (baseExistente, DateTime.UtcNow.AddHours(12));

            }

        }



        int estimadoInicial = totalEstimado > 0 ? totalEstimado : (baseExistente?.Count ?? 0);

        bool usarIncremental = !forzar && baseExistente is not null && baseExistente.Count > 0;



        if (!_enVueloCuentas.ContainsKey(nombreInternoEmpresa))

        {

            _progresoCuentas[nombreInternoEmpresa] = new ProgresoCuentasDto(

                nombreInternoEmpresa,

                EnCurso: true,

                Terminado: false,

                Leidas: usarIncremental ? baseExistente!.Count : 0,

                TotalEstimado: estimadoInicial,

                Porcentaje: usarIncremental ? 15.0 : 2.0,

                Segundos: 0.0,

                CuentasPorSegundo: 0.0,

                Etapa: usarIncremental

                    ? $"Conectando con CONTPAQi para verificar cuentas nuevas (base: {baseExistente!.Count:N0} cuentas)..."

                    : "Conectando con sesión de CONTPAQi...",

                UltimaCuenta: null,

                DesdeCache: false,

                Error: null,

                Nuevas: 0,

                Modo: usarIncremental ? "incremental" : "completo"

            );

        }



        var tarea = _enVueloCuentas.GetOrAdd(nombreInternoEmpresa, emp => _dispatcher.EjecutarAsync<ResultadoCuentasDto>(() =>

        {

            var sw = Stopwatch.StartNew();

            try

            {

                AsegurarEmpresaAbierta(emp);



                var tipoCuenta = Type.GetTypeFromProgID("SDKCONTPAQNG.TSdkCuenta")!;

                dynamic cuenta = Activator.CreateInstance(tipoCuenta)!;



                try

                {

                    cuenta.setSesion(_sesion);



                    if (usarIncremental && baseExistente is not null && baseExistente.Count > 0)

                    {

                        return (ResultadoCuentasDto)SincronizarIncrementalPorId(emp, cuenta, baseExistente, sw);

                    }



                    return (ResultadoCuentasDto)LeerCatalogoCompleto(emp, cuenta, estimadoInicial, sw);

                }

                finally

                {

                    try { Marshal.ReleaseComObject(cuenta); } catch { }

                }

            }

            catch (Exception ex)

            {

                sw.Stop();

                _progresoCuentas[emp] = new ProgresoCuentasDto(

                    emp,

                    EnCurso: false,

                    Terminado: true,

                    Leidas: 0,

                    TotalEstimado: estimadoInicial,

                    Porcentaje: 0.0,

                    Segundos: Math.Round(sw.Elapsed.TotalSeconds, 1),

                    CuentasPorSegundo: 0.0,

                    Etapa: "Error al leer desde CONTPAQi",

                    UltimaCuenta: null,

                    DesdeCache: false,

                    Error: ex.Message,

                    Nuevas: 0,

                    Modo: usarIncremental ? "incremental" : "completo"

                );

                throw;

            }

        }, TimeSpan.FromSeconds(900)));



        try

        {

            return await tarea;

        }

        finally

        {

            _enVueloCuentas.TryRemove(nombreInternoEmpresa, out _);

        }

    }



    private ResultadoCuentasDto SincronizarIncrementalPorId(

        string emp,

        dynamic cuenta,

        List<CuentaDto> baseExistente,

        Stopwatch sw)

    {

        var mapa = new Dictionary<string, CuentaDto>(StringComparer.OrdinalIgnoreCase);

        var codigoPorId = new Dictionary<int, string>();

        var huellasExistentes = new HashSet<string>(StringComparer.Ordinal);



        foreach (var c in baseExistente)

        {

            if (!string.IsNullOrWhiteSpace(c.Codigo))

            {

                var dtoNorm = c with { IdComparacion = c.ObtenerIdComparacion() };

                mapa[c.Codigo] = dtoNorm;

                huellasExistentes.Add(dtoNorm.IdComparacion!);

                if (c.IdContpaqi > 0)

                {

                    codigoPorId[c.IdContpaqi] = c.Codigo;

                }

            }

        }



        void AplicarCuentaDetectada(int idContpaqi, string codigo, string nombre, ref int contadorCambios, ref string? ultima)

        {

            string idComp = $"{idContpaqi}|{codigo}|{nombre}";

            if (huellasExistentes.Contains(idComp))

            {

                return;

            }



            // Si el mismo IdContpaqi tenía antes otro número de cuenta, reemplazar el anterior

            if (idContpaqi > 0 && codigoPorId.TryGetValue(idContpaqi, out var codigoAnterior) &&

                !string.Equals(codigoAnterior, codigo, StringComparison.OrdinalIgnoreCase))

            {

                mapa.Remove(codigoAnterior);

            }



            mapa.TryGetValue(codigo, out var previa);

            // Si existía sin IdContpaqi pero con el mismo código y nombre exacto, solo actualizamos su huella sin contarlo como cambio

            bool eraMismaSinId = previa is not null && previa.IdContpaqi == 0 &&

                                 string.Equals(previa.Nombre, nombre, StringComparison.Ordinal);



            var nuevaDto = new CuentaDto(codigo, nombre, previa?.AgrupadorSat, idContpaqi, idComp);

            mapa[codigo] = nuevaDto;

            huellasExistentes.Add(idComp);

            if (idContpaqi > 0)

            {

                codigoPorId[idContpaqi] = codigo;

            }



            if (!eraMismaSinId)

            {

                contadorCambios++;

                ultima = $"{codigo} — {nombre} [ID:{idContpaqi}]";

            }

        }



        var meta = IntentarLeerMetaDisco(emp);

        int ultimoIdConocido = meta?.UltimoId ?? 0;

        int nuevasOActualizadas = 0;

        string? ultimaDetectada = null;



        _log.LogInformation(

            "Iniciando sincronización INCREMENTAL por IdComparacion para {Empresa} (base={Base}, ultimoIdConocido={UltimoId})",

            emp, mapa.Count, ultimoIdConocido);



        _progresoCuentas[emp] = new ProgresoCuentasDto(

            emp,

            EnCurso: true,

            Terminado: false,

            Leidas: mapa.Count,

            TotalEstimado: mapa.Count,

            Porcentaje: 35.0,

            Segundos: Math.Round(sw.Elapsed.TotalSeconds, 1),

            CuentasPorSegundo: 0.0,

            Etapa: $"Comparando huella (ID+Cuenta+Nombre) contra {mapa.Count:N0} cuentas registradas...",

            UltimaCuenta: null,

            DesdeCache: false,

            Error: null,

            Nuevas: 0,

            Modo: "incremental"

        );



        int maxIdActual = ultimoIdConocido;



        if (ultimoIdConocido > 0)

        {

            // Revisamos también las últimas 3 cuentas previas por si acaban de editarse, y luego IDs nuevos hacia adelante

            int misses = 0;

            int idCursor = Math.Max(1, ultimoIdConocido - 2);

            while (misses < 8)

            {

                if (BuscarCuentaPorId(cuenta, idCursor))

                {

                    if (idCursor > ultimoIdConocido)

                    {

                        misses = 0;

                    }

                    if (idCursor > maxIdActual)

                    {

                        maxIdActual = idCursor;

                    }

                    string codigo = ((string)cuenta.Codigo).Replace("\0", "").Trim();

                    string nombre = ((string)cuenta.Nombre).Replace("\0", "").Trim();

                    if (!string.IsNullOrEmpty(codigo) && codigo != "_CUADRE" && !codigo.StartsWith("_"))

                    {

                        int cambiosAntes = nuevasOActualizadas;

                        AplicarCuentaDetectada(idCursor, codigo, nombre, ref nuevasOActualizadas, ref ultimaDetectada);

                        if (nuevasOActualizadas > cambiosAntes)

                        {

                            _progresoCuentas[emp] = new ProgresoCuentasDto(

                                emp,

                                EnCurso: true,

                                Terminado: false,

                                Leidas: mapa.Count,

                                TotalEstimado: mapa.Count,

                                Porcentaje: 80.0,

                                Segundos: Math.Round(sw.Elapsed.TotalSeconds, 1),

                                CuentasPorSegundo: Math.Round(nuevasOActualizadas / Math.Max(0.1, sw.Elapsed.TotalSeconds), 1),

                                Etapa: $"Detectada diferencia #{nuevasOActualizadas}: {codigo}",

                                UltimaCuenta: ultimaDetectada,

                                DesdeCache: false,

                                Error: null,

                                Nuevas: nuevasOActualizadas,

                                Modo: "incremental"

                            );

                        }

                    }

                }

                else if (idCursor > ultimoIdConocido)

                {

                    misses++;

                }

                idCursor++;

            }

        }

        else

        {

            // Primera calibración de UltimoId sobre un catálogo ya existente:

            int low = Math.Max(1, mapa.Count);

            int encontradosLow = BuscarIdEnVentana(cuenta, low, ventana: 6);

            if (encontradosLow == 0 && low > 20)

            {

                low = Math.Max(1, low - 20);

            }



            int step = 32;

            int high = low + step;

            int ultimoHit = Math.Max(low, encontradosLow);



            while (step <= 4096)

            {

                int hit = BuscarIdEnVentana(cuenta, high, ventana: 6);

                if (hit > 0)

                {

                    ultimoHit = hit;

                    low = hit;

                    step *= 2;

                    high = low + step;

                }

                else

                {

                    break;

                }

            }



            int izq = low;

            int der = high;

            while (der - izq > 8)

            {

                int mid = izq + (der - izq) / 2;

                int hit = BuscarIdEnVentana(cuenta, mid, ventana: 5);

                if (hit > 0)

                {

                    if (hit > ultimoHit) ultimoHit = hit;

                    izq = hit;

                }

                else

                {

                    der = mid;

                }

            }



            int missesAdelante = 0;

            int cursorAdelante = Math.Max(1, izq);

            while (missesAdelante < 15)

            {

                if (BuscarCuentaPorId(cuenta, cursorAdelante))

                {

                    missesAdelante = 0;

                    if (cursorAdelante > ultimoHit) ultimoHit = cursorAdelante;

                }

                else

                {

                    missesAdelante++;

                }

                cursorAdelante++;

            }



            maxIdActual = ultimoHit;

            _log.LogInformation("Calibrado MaxId={MaxId} para {Empresa} en {Seg:F1}s", maxIdActual, emp, sw.Elapsed.TotalSeconds);



            _progresoCuentas[emp] = new ProgresoCuentasDto(

                emp,

                EnCurso: true,

                Terminado: false,

                Leidas: mapa.Count,

                TotalEstimado: mapa.Count,

                Porcentaje: 75.0,

                Segundos: Math.Round(sw.Elapsed.TotalSeconds, 1),

                CuentasPorSegundo: 0.0,

                Etapa: $"Verificando últimas cuentas registradas en CONTPAQi (ID máx: {maxIdActual})...",

                UltimaCuenta: null,

                DesdeCache: false,

                Error: null,

                Nuevas: 0,

                Modo: "incremental"

            );



            int conocidasConsecutivas = 0;

            for (int id = maxIdActual; id >= 1 && conocidasConsecutivas < 12; id--)

            {

                if (BuscarCuentaPorId(cuenta, id))

                {

                    string codigo = ((string)cuenta.Codigo).Replace("\0", "").Trim();

                    string nombre = ((string)cuenta.Nombre).Replace("\0", "").Trim();

                    if (!string.IsNullOrEmpty(codigo) && codigo != "_CUADRE" && !codigo.StartsWith("_"))

                    {

                        int antes = nuevasOActualizadas;

                        AplicarCuentaDetectada(id, codigo, nombre, ref nuevasOActualizadas, ref ultimaDetectada);

                        if (nuevasOActualizadas > antes)

                        {

                            conocidasConsecutivas = 0;

                        }

                        else

                        {

                            conocidasConsecutivas++;

                        }

                    }

                }

            }

        }



        sw.Stop();

        var listaFinal = mapa.Values.OrderBy(c => c.Codigo, StringComparer.OrdinalIgnoreCase).ToList();

        _cacheCuentas[emp] = (listaFinal, DateTime.UtcNow.AddHours(12));

        GuardarCacheDisco(emp, listaFinal);

        if (maxIdActual > 0)

        {

            GuardarMetaDisco(emp, maxIdActual, listaFinal.Count);

        }



        double segTotal = Math.Max(0.1, sw.Elapsed.TotalSeconds);

        string msgEtapa = nuevasOActualizadas > 0

            ? $"¡Sincronización incremental lista! {nuevasOActualizadas} cuenta(s) nueva(s) agregada(s) ({listaFinal.Count:N0} en total)."

            : $"¡Catálogo al día! 0 cuentas nuevas en CONTPAQi ({listaFinal.Count:N0} cuentas verificadas en {segTotal:F1}s).";



        _log.LogInformation(

            "Sincronización incremental completada para {Empresa}: +{Nuevas} nuevas, total={Total}, maxId={MaxId} en {Seg:F1}s",

            emp, nuevasOActualizadas, listaFinal.Count, maxIdActual, segTotal);



        _progresoCuentas[emp] = new ProgresoCuentasDto(

            emp,

            EnCurso: false,

            Terminado: true,

            Leidas: listaFinal.Count,

            TotalEstimado: listaFinal.Count,

            Porcentaje: 100.0,

            Segundos: Math.Round(segTotal, 1),

            CuentasPorSegundo: Math.Round(listaFinal.Count / segTotal, 1),

            Etapa: msgEtapa,

            UltimaCuenta: ultimaDetectada,

            DesdeCache: false,

            Error: null,

            Nuevas: nuevasOActualizadas,

            Modo: "incremental"

        );



        return new ResultadoCuentasDto(listaFinal, nuevasOActualizadas, "incremental");

    }



    private ResultadoCuentasDto LeerCatalogoCompleto(

        string emp,

        dynamic cuenta,

        int estimadoInicial,

        Stopwatch sw)

    {

        var resultado = new List<CuentaDto>(estimadoInicial > 0 ? estimadoInicial : 2048);

        int exito = 0;

        string? ultimoCodigo = null;

        int maxId = 0;

        _log.LogInformation("Iniciando lectura COMPLETA de cuentas para {Empresa}...", emp);



        _progresoCuentas[emp] = new ProgresoCuentasDto(

            emp,

            EnCurso: true,

            Terminado: false,

            Leidas: 0,

            TotalEstimado: estimadoInicial,

            Porcentaje: 6.0,

            Segundos: Math.Round(sw.Elapsed.TotalSeconds, 1),

            CuentasPorSegundo: 0.0,

            Etapa: "Leyendo catálogo completo desde CONTPAQi...",

            UltimaCuenta: null,

            DesdeCache: false,

            Error: null,

            Nuevas: 0,

            Modo: "completo"

        );



        cuenta.consultaPorCodigo_buscaPrimero(ref exito);

        if (exito == 1)

        {

            do

            {

                try 

                {

                    string codigo = ((string)cuenta.Codigo).Replace("\0", "").Trim();

                    if (string.IsNullOrEmpty(codigo) || codigo == ultimoCodigo)

                    {

                        break;

                    }

                    ultimoCodigo = codigo;

                    string nombre = ((string)cuenta.Nombre).Replace("\0", "").Trim();

                    int idActual = ObtenerIdCuenta(cuenta);

                    if (idActual > maxId) maxId = idActual;

                    

                    if (codigo != "_CUADRE" && !codigo.StartsWith("_"))

                    {

                        string idComp = $"{idActual}|{codigo}|{nombre}";

                        resultado.Add(new CuentaDto(codigo, nombre, null, idActual, idComp));

                        int n = resultado.Count;

                        if (n % 5 == 0 || n == 1)

                        {

                            double seg = Math.Max(0.1, sw.Elapsed.TotalSeconds);

                            double vel = Math.Round(n / seg, 1);

                            double pct;

                            int estActual = estimadoInicial;

                            if (estActual > 0)

                            {

                                if (n >= estActual) estActual = n + 50;

                                pct = Math.Min(95.0, Math.Round(6.0 + ((double)n / estActual) * 89.0, 1));

                            }

                            else

                            {

                                pct = Math.Min(92.0, Math.Round(6.0 + (1.0 - Math.Exp(-n / 1800.0)) * 86.0, 1));

                            }



                            _progresoCuentas[emp] = new ProgresoCuentasDto(

                                emp,

                                EnCurso: true,

                                Terminado: false,

                                Leidas: n,

                                TotalEstimado: estActual,

                                Porcentaje: pct,

                                Segundos: Math.Round(seg, 1),

                                CuentasPorSegundo: vel,

                                Etapa: "Extrayendo cuentas desde CONTPAQi (lectura completa)...",

                                UltimaCuenta: $"{codigo} — {nombre}",

                                DesdeCache: false,

                                Error: null,

                                Nuevas: n,

                                Modo: "completo"

                            );

                        }

                        if (n % 100 == 0)

                        {

                            _log.LogInformation("Leídas {N} cuentas de {Empresa} en {Seg:F1}s (última: {Cod})", n, emp, sw.Elapsed.TotalSeconds, codigo);

                        }

                    }

                }

                catch (Exception ex)

                {

                    _log.LogWarning("Error leyendo cuenta en {Empresa}: {Msg}", emp, ex.Message);

                    break;

                }

                exito = 0;

                cuenta.consultaPorCodigo_buscaSiguiente(ref exito);

            } while (exito == 1);

        }

        sw.Stop();

        double segTotal = Math.Max(0.1, sw.Elapsed.TotalSeconds);

        _log.LogInformation("Lectura completa finalizada para {Empresa}: {N} cuentas (maxId={MaxId}) en {Seg:F1}s", emp, resultado.Count, maxId, segTotal);

        if (resultado.Count > 0)

        {

            _cacheCuentas[emp] = (resultado, DateTime.UtcNow.AddHours(12));

            GuardarCacheDisco(emp, resultado);

            if (maxId > 0)

            {

                GuardarMetaDisco(emp, maxId, resultado.Count);

            }

        }

        _progresoCuentas[emp] = new ProgresoCuentasDto(

            emp,

            EnCurso: false,

            Terminado: true,

            Leidas: resultado.Count,

            TotalEstimado: resultado.Count,

            Porcentaje: 100.0,

            Segundos: Math.Round(segTotal, 1),

            CuentasPorSegundo: Math.Round(resultado.Count / segTotal, 1),

            Etapa: $"Lectura completa de CONTPAQi finalizada ({resultado.Count:N0} cuentas)",

            UltimaCuenta: resultado.Count > 0 ? $"{resultado[^1].Codigo} — {resultado[^1].Nombre}" : null,

            DesdeCache: false,

            Error: null,

            Nuevas: resultado.Count,

            Modo: "completo"

        );

        return new ResultadoCuentasDto(resultado, resultado.Count, "completo");

    }



    public async Task<List<CuentaDto>> ListarCuentasAsync(string nombreInternoEmpresa, bool forzar = false, int totalEstimado = 0)

    {

        var res = await ListarCuentasConDetalleAsync(nombreInternoEmpresa, forzar, totalEstimado);

        return res.Cuentas;

    }



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
                Type? tipoAsoc = Type.GetTypeFromProgID("SDKCONTPAQNG.TSdkAsocCFDI");
                Type? tipoControlIVA = Type.GetTypeFromProgID("SDKCONTPAQNG.TSdkControlIVA");
                Type? tipoCausacionIVA = Type.GetTypeFromProgID("SDKCONTPAQNG.TSdkCausacionIVA");
                
                int creadas = 0;
                List<string> errores = new();

                foreach (var p in polizas)
                {
                    dynamic pol = Activator.CreateInstance(tipoPoliza)!;
                    try
                    {
                        pol.setSesion(_sesion);
                        
                        DateTime fechaPoliza = DateTime.Today;
                        if (DateTime.TryParse(p.Fecha, out DateTime parsedDate))
                        {
                            fechaPoliza = parsedDate.Date;
                        }

                        int numPol = int.Parse(p.Numero);

                        // Si la póliza ya existe en CONTPAQi, eliminarla previamente para recrearla limpia con movimientos, F4/F6 y CFDI
                        try
                        {
                            if (pol.consultaPorEjercicioPeriodoTipoNumero_buscaPorLlave(fechaPoliza.Year, fechaPoliza.Month, p.Tipo, numPol) == 1)
                            {
                                pol.borra();
                            }
                        }
                        catch { }

                        pol.iniciarInfo();
                        pol.Fecha = fechaPoliza;
                        pol.Tipo = p.Tipo;
                        pol.Numero = numPol;
                        pol.Clase = 1;
                        pol.SistOrigen = 1;
                        pol.Concepto = p.Concepto;
                        pol.Impresa = 0;
                        
                        int numMovto = 1;
                        foreach (var m in p.Movimientos)
                        {
                            dynamic mov = Activator.CreateInstance(tipoMov)!;
                            try
                            {
                                mov.setSesion(_sesion);
                                mov.iniciarInfo();
                                
                                mov.NumMovto = numMovto++;
                                mov.CodigoCuenta = m.Cuenta;
                                // 1 = Cargo, 2 = Abono en CONTPAQi SDK
                                mov.TipoMovto = (m.TipoMovto == 2 || m.TipoMovto == 1) ? m.TipoMovto : (m.TipoMovto == 0 ? 1 : 2);
                                mov.Importe = (double)m.Importe;
                                mov.Concepto = string.IsNullOrEmpty(m.Concepto) ? p.Concepto : m.Concepto;
                                mov.Referencia = m.Referencia ?? "";
                                
                                int resMov = pol.agregaMovimiento(mov);
                                if (resMov != 1 && resMov != 0)
                                {
                                    _log.LogWarning("Respuesta agregaMovimiento a cuenta {Cuenta}: {Res}", m.Cuenta, resMov);
                                }
                            }
                            finally
                            {
                                try { Marshal.ReleaseComObject(mov); } catch { }
                            }
                        }

                        // Agregar Control de IVA Fiscal (F4 para Egresos/Diarios, F6 para Ingresos) si afecta IVA fiscal
                        if (p.AfectarIvaFiscal && p.ControlIva != null && p.ControlIva.Count > 0)
                        {
                            if ((p.Tipo == 2 || p.Tipo == 3) && tipoControlIVA is not null) // Egreso o Diario -> F4
                            {
                                foreach (var c in p.ControlIva)
                                {
                                    dynamic ctrl = Activator.CreateInstance(tipoControlIVA)!;
                                    try
                                    {
                                        ctrl.setSesion(_sesion);
                                        ctrl.iniciarInfo();

                                        if (!string.IsNullOrWhiteSpace(c.Uuid))
                                        {
                                            try { ctrl.UUID = c.Uuid.Trim().ToUpper(); } catch { }
                                        }
                                        if (!string.IsNullOrWhiteSpace(c.Serie))
                                        {
                                            try
                                            {
                                                string sStr = c.Serie.Trim();
                                                if (sStr.Length > 20) sStr = sStr.Substring(0, 20);
                                                ctrl.Serie = sStr;
                                            }
                                            catch { }
                                        }
                                        if (!string.IsNullOrWhiteSpace(c.Folio))
                                        {
                                            string fStr = c.Folio.Trim();
                                            // En TSdkControlIVA, Referencia es string y Folio es int
                                            try { ctrl.Referencia = fStr.Length > 20 ? fStr.Substring(0, 20) : fStr; } catch { }
                                            try
                                            {
                                                if (int.TryParse(fStr, out int fInt))
                                                {
                                                    ctrl.Folio = fInt;
                                                }
                                                else
                                                {
                                                    string soloDigitos = System.Text.RegularExpressions.Regex.Replace(fStr, @"\D", "");
                                                    if (!string.IsNullOrEmpty(soloDigitos) && int.TryParse(soloDigitos, out int fNum))
                                                    {
                                                        ctrl.Folio = fNum;
                                                    }
                                                }
                                            }
                                            catch { }
                                        }
                                        if (!string.IsNullOrWhiteSpace(c.Cuenta))
                                        {
                                            try { ctrl.CodigoCuenta = c.Cuenta.Replace("-", "").Replace(" ", "").Trim(); } catch { }
                                        }

                                        try { ctrl.ImporteBase = (double)c.Base; } catch { }
                                        try { ctrl.ImporteIVA = (double)c.Iva; } catch { }
                                        try { ctrl.ImporteTotal = (double)c.Total; } catch { }
                                        try { ctrl.TasaIVA = (double)c.Tasa; } catch { }
                                        try
                                        {
                                            if (c.RetencionIva > 0) ctrl.ImporteRetencionesIVA = (double)c.RetencionIva;
                                            if (c.RetencionIsr > 0) ctrl.ImporteRetencionesISR = (double)c.RetencionIsr;
                                        }
                                        catch { }

                                        try { ctrl.EjercicioAcreditamiento = fechaPoliza.Year; } catch { }
                                        try { ctrl.PeriodoAcreditamiento = fechaPoliza.Month; } catch { }
                                        try { ctrl.CausaIVA = 1; } catch { }

                                        try
                                        {
                                            int resCtrl = pol.agregaControlIVA(ctrl);
                                            if (resCtrl != 1 && resCtrl != 0)
                                            {
                                                _log.LogWarning("Aviso al agregar Control de IVA (F4) a póliza {Num}: {Res}", p.Numero, resCtrl);
                                            }
                                        }
                                        catch (Exception exAddCtrl)
                                        {
                                            _log.LogWarning(exAddCtrl, "No se pudo invocar agregaControlIVA para póliza {Num}", p.Numero);
                                        }
                                    }
                                    finally
                                    {
                                        try { Marshal.ReleaseComObject(ctrl); } catch { }
                                    }
                                }
                            }
                            else if (p.Tipo == 1 && tipoCausacionIVA is not null) // Ingreso -> F6
                            {
                                dynamic causa = Activator.CreateInstance(tipoCausacionIVA)!;
                                try
                                {
                                    causa.setSesion(_sesion);
                                    causa.iniciarInfo();
                                    decimal totBase = 0, totIva = 0, totGral = 0, totRetIva = 0, totRetIsr = 0;
                                    foreach (var c in p.ControlIva)
                                    {
                                        totBase += c.Base;
                                        totIva += c.Iva;
                                        totGral += c.Total;
                                        totRetIva += c.RetencionIva;
                                        totRetIsr += c.RetencionIsr;
                                    }
                                    try { causa.BaseTasa16 = (double)totBase; } catch { }
                                    try { causa.IVATasa16 = (double)totIva; } catch { }
                                    try { causa.TotTasa16 = (double)totGral; } catch { }
                                    try { if (totRetIva > 0) causa.IVARetenido = (double)totRetIva; } catch { }
                                    try { if (totRetIsr > 0) causa.ISRRetenido = (double)totRetIsr; } catch { }

                                    try
                                    {
                                        int resCausa = pol.setCausacionIVA(causa);
                                        if (resCausa != 1 && resCausa != 0)
                                        {
                                            _log.LogWarning("Aviso al agregar Causación de IVA (F6) a póliza {Num}: {Res}", p.Numero, resCausa);
                                        }
                                    }
                                    catch (Exception exAddCausa)
                                    {
                                        _log.LogWarning(exAddCausa, "No se pudo invocar setCausacionIVA para póliza {Num}", p.Numero);
                                    }
                                }
                                finally
                                {
                                    try { Marshal.ReleaseComObject(causa); } catch { }
                                }
                            }
                        }

                        int idPoliza = pol.crea();
                        if (idPoliza <= 0)
                        {
                            string err = _sesion.UltimoMsjError;
                            _log.LogError("Error al crear póliza {Numero} ({Fecha} {Concepto}): {Error}", p.Numero, p.Fecha, p.Concepto, err);
                            errores.Add($"Póliza {p.Numero}: {err}");
                            continue;
                        }
                        
                        _log.LogInformation("Póliza {Numero} creada exitosamente con ID {IdPoliza}", p.Numero, idPoliza);

                        // Asociar CFDIs al encabezado de la póliza (TipoAsoc = 0)
                        if (p.CfdiUuids != null && p.CfdiUuids.Count > 0 && tipoAsoc is not null)
                        {
                            dynamic asoc = Activator.CreateInstance(tipoAsoc)!;
                            try
                            {
                                asoc.setSesion(_sesion);
                                asoc.iniciarInfo();
                                
                                string guidPoliza = pol.Guid;
                                asoc.GuidDocumento = guidPoliza;
                                asoc.TipoAsoc = 0; // 0 = Encabezado de Póliza
                                
                                int agregados = 0;
                                foreach (var uuid in p.CfdiUuids)
                                {
                                    if (!string.IsNullOrWhiteSpace(uuid))
                                    {
                                        asoc.agregaUUID(uuid.Trim());
                                        agregados++;
                                    }
                                }
                                
                                if (agregados > 0)
                                {
                                    int resAsoc = asoc.crea();
                                    if (resAsoc != 0 && resAsoc != 1)
                                    {
                                        _log.LogWarning("Aviso al finalizar asoc.crea() para la póliza {Guid}: {Res}", guidPoliza, resAsoc);
                                    }
                                    else
                                    {
                                        _log.LogInformation("CFDIs ({N}) asociados exitosamente a la póliza {Guid}", agregados, guidPoliza);
                                    }
                                }
                            }
                            catch (Exception exCFDI)
                            {
                                _log.LogError(exCFDI, "Excepción al intentar asociar CFDI a la póliza");
                            }
                            finally
                            {
                                try { Marshal.ReleaseComObject(asoc); } catch { }
                            }
                        }

                        creadas++;
                    }
                    catch (Exception exPol)
                    {
                        _log.LogError(exPol, "Error procesando póliza {Numero} ({Fecha} {Concepto})", p.Numero, p.Fecha, p.Concepto);
                        errores.Add($"Póliza {p.Numero}: {exPol.Message}");
                    }
                    finally
                    {
                        try { Marshal.ReleaseComObject(pol); } catch { }
                    }
                }

                if (creadas == 0 && errores.Count > 0)
                {
                    return new RespuestaBridge(false, "No se pudo inyectar ninguna póliza: " + string.Join("; ", errores));
                }

                string msg = $"Se inyectaron {creadas} pólizas exitosamente en CONTPAQi.";
                if (errores.Count > 0)
                {
                    msg += $" Hubo {errores.Count} fallidas: " + string.Join("; ", errores);
                }
                return new RespuestaBridge(true, msg, new { Creadas = creadas, Errores = errores });
            }
            catch (Exception ex)
            {
                _log.LogError(ex, "Excepción en CrearPolizasAsync");
                return new RespuestaBridge(false, "Error desconocido: " + ex.Message);
            }
        });
    }

    public void Dispose()

    {

        if (_sesion is not null)

        {

            try

            {

                if (_empresaAbiertaActual is not null)

                {

                    _sesion.cierraEmpresa();

                }

                _sesion.finalizaConexion();

                Marshal.ReleaseComObject(_sesion);

            }

            catch { }

            _sesion = null;

            _empresaAbiertaActual = null;

        }

    }

}

