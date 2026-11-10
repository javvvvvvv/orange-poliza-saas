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

namespace ContpaqiBridge.Services;

/// <summary>
/// El SDK de CONTPAQi es un objeto COM de apartamento STA. Kestrel atiende
/// cada request en un hilo del thread pool (MTA), y llamar un objeto STA
/// desde ahí produce fallas intermitentes (RPC_E_WRONG_THREAD, deadlocks,
/// "server busy") — este es el sospechoso número uno de por qué la
/// conexión "no lo hacía bien" antes.
///
/// La solución: un único hilo STA dedicado que vive toda la vida del
/// proceso, con una cola de trabajos. Todo lo que toca el SDK pasa por
/// aquí, nunca se llama el COM object directo desde un endpoint.
/// </summary>
public sealed class StaComDispatcher : IDisposable
{
    private readonly BlockingCollection<Action> _cola = new();
    private readonly Thread _hiloSta;

    public StaComDispatcher()
    {
        _hiloSta = new Thread(Bucle)
        {
            IsBackground = true,
            Name = "ContpaqiBridge-STA-COM",
        };
        _hiloSta.SetApartmentState(ApartmentState.STA);
        _hiloSta.Start();
    }

    private void Bucle()
    {
        foreach (var trabajo in _cola.GetConsumingEnumerable())
        {
            trabajo();
        }
    }

    /// <summary>Ejecuta una función en el hilo STA y regresa su resultado con timeout de seguridad.</summary>
    public async Task<T> EjecutarAsync<T>(Func<T> funcion, TimeSpan? timeout = null)
    {
        var limite = timeout ?? TimeSpan.FromSeconds(900);
        using var cts = new CancellationTokenSource(limite);
        var tcs = new TaskCompletionSource<T>(TaskCreationOptions.RunContinuationsAsynchronously);
        _cola.Add(() =>
        {
            if (cts.IsCancellationRequested)
            {
                tcs.TrySetCanceled(cts.Token);
                return;
            }
            try
            {
                tcs.TrySetResult(funcion());
            }
            catch (Exception ex)
            {
                tcs.TrySetException(ex);
            }
        });

        var completedTask = await Task.WhenAny(tcs.Task, Task.Delay(Timeout.Infinite, cts.Token));
        if (completedTask == tcs.Task)
        {
            cts.Cancel();
            return await tcs.Task;
        }

        cts.Cancel();
        throw new TimeoutException($"La operación en el SDK de CONTPAQi excedió el tiempo límite de {limite.TotalSeconds} segundos.");
    }

    public void Dispose()
    {
        _cola.CompleteAdding();
    }
}
