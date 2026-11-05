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
using ContpaqiBridge.Models;
using ContpaqiBridge.Services;

var builder = WebApplication.CreateBuilder(args);

builder.Configuration
    .AddJsonFile("appsettings.json", optional: true, reloadOnChange: true)
    .AddEnvironmentVariables(prefix: "CONTPAQI_BRIDGE_");

var puerto = builder.Configuration.GetValue("Puerto", 5005);
var hostBind = Environment.GetEnvironmentVariable("CONTPAQI_BRIDGE_HOST") ?? builder.Configuration.GetValue("Host", "127.0.0.1");
var apiKeyConfig = Environment.GetEnvironmentVariable("CONTPAQI_BRIDGE_API_KEY") ?? builder.Configuration.GetValue("ApiKey", "orange_bridge_secret_2026");

builder.WebHost.ConfigureKestrel(opciones =>
{
    if (hostBind == "0.0.0.0" || hostBind == "Any")
    {
        opciones.ListenAnyIP(puerto);
    }
    else
    {
        opciones.Listen(System.Net.IPAddress.Parse(hostBind), puerto);
    }
});

builder.Services.AddSingleton<StaComDispatcher>();
builder.Services.AddSingleton<ContpaqiSdkService>();

var app = builder.Build();

// Middleware de autenticación por API Key (si está configurada)
app.Use(async (context, next) =>
{
    if (!string.IsNullOrWhiteSpace(apiKeyConfig) && context.Request.Path != "/salud")
    {
        var authHeader = context.Request.Headers["X-Bridge-ApiKey"].FirstOrDefault() 
                         ?? context.Request.Headers["Authorization"].FirstOrDefault();
        if (string.IsNullOrWhiteSpace(authHeader))
        {
            context.Response.StatusCode = StatusCodes.Status401Unauthorized;
            await context.Response.WriteAsJsonAsync(new RespuestaBridge(false, "Falta cabecera X-Bridge-ApiKey."));
            return;
        }

        var token = authHeader.StartsWith("Bearer ", StringComparison.OrdinalIgnoreCase) 
            ? authHeader.Substring(7).Trim() 
            : authHeader.Trim();

        if (token != apiKeyConfig)
        {
            context.Response.StatusCode = StatusCodes.Status403Forbidden;
            await context.Response.WriteAsJsonAsync(new RespuestaBridge(false, "X-Bridge-ApiKey inválida."));
            return;
        }
    }
    await next();
});

app.MapGet("/salud", async (ContpaqiSdkService sdk) =>
{
    var disponible = await sdk.VerificarSdkDisponibleAsync();
    return disponible
        ? Results.Ok(new RespuestaBridge(true, "SDK de CONTPAQi disponible."))
        : Results.StatusCode(StatusCodes.Status503ServiceUnavailable);
});

app.MapGet("/empresas/listar", async (ContpaqiSdkService sdk, ILogger<Program> log) =>
{
    try
    {
        var empresas = await sdk.ListarEmpresasAsync();
        return Results.Ok(new { exito = true, empresas });
    }
    catch (Exception ex)
    {
        log.LogError(ex, "Fallo al listar empresas");
        return Results.Ok(new RespuestaBridge(false, $"Error del SDK: {ex.Message}"));
    }
});

app.MapPost("/cuentas/crear", async (CrearCuentaRequest req, ContpaqiSdkService sdk, ILogger<Program> log) =>
{
    if (string.IsNullOrWhiteSpace(req.Empresa)) return Results.BadRequest(new RespuestaBridge(false, "Falta la empresa."));
    try
    {
        var result = await sdk.CrearCuentaAsync(req.Empresa, req.Cuenta);
        return Results.Ok(result);
    }
    catch (Exception ex)
    {
        log.LogError(ex, "Fallo al crear cuenta en {Empresa}", req.Empresa);
        return Results.Ok(new RespuestaBridge(false, $"Error del SDK al crear cuenta: {ex.Message}"));
    }
});

app.MapPost("/cuentas/listar", async (CuentasRequest req, ContpaqiSdkService sdk, ILogger<Program> log) =>
{
    if (string.IsNullOrWhiteSpace(req.Empresa))
    {
        return Results.BadRequest(new RespuestaBridge(false, "Falta el nombre interno de la empresa."));
    }
    try
    {
        var cuentas = await sdk.ListarCuentasAsync(req.Empresa);
        return Results.Ok(new { exito = true, mensaje = "OK", cuentas });
    }
    catch (Exception ex)
    {
        log.LogError(ex, "Fallo al listar cuentas para {Empresa}", req.Empresa);
        return Results.Ok(new RespuestaBridge(false, $"Error del SDK al listar cuentas: {ex.Message}"));
    }
});

app.MapPost("/", async (ExportarPolizasRequest req, ContpaqiSdkService sdk, ILogger<Program> log) =>
{
    if (string.IsNullOrWhiteSpace(req.Empresa))
    {
        return Results.BadRequest(new RespuestaBridge(false, "Falta el nombre interno de la empresa."));
    }
    try
    {
        var resultado = await sdk.CrearPolizasAsync(req.Empresa, req.Polizas);
        return Results.Ok(resultado);
    }
    catch (Exception ex)
    {
        log.LogError(ex, "Fallo al exportar pólizas para {Empresa}", req.Empresa);
        return Results.Ok(new RespuestaBridge(false, $"Error del SDK al exportar pólizas: {ex.Message}"));
    }
});

app.Logger.LogInformation("ContpaqiBridge escuchando en {Host}:{Puerto}", hostBind, puerto);
app.Run();
