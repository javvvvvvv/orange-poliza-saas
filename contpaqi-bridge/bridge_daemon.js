// ============================================================================
// PROPIEDAD INTELECTUAL Y LICENCIA COMERCIAL CERRADA - ORANGE CREW
// ============================================================================
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

const dotnetPath = fs.existsSync("C:\\Program Files (x86)\\dotnet\\dotnet.exe")
  ? "C:\\Program Files (x86)\\dotnet\\dotnet.exe"
  : "dotnet";

const dllPath = path.join(__dirname, 'bin', 'Debug', 'net10.0', 'ContpaqiBridge.dll');

console.log(`[Bridge Daemon] Iniciando ContpaqiBridge con: ${dotnetPath}`);
console.log(`[Bridge Daemon] DLL: ${dllPath}`);

const cleanEnv = { ...process.env };
for (const k of Object.keys(cleanEnv)) {
  if (k.startsWith('ANTIGRAVITY_')) {
    delete cleanEnv[k];
  }
}

const child = spawn(dotnetPath, [dllPath, '--urls', 'http://0.0.0.0:5005'], {
  cwd: __dirname,
  stdio: 'inherit',
  env: cleanEnv
});

child.on('error', (err) => {
  console.error('[Bridge Daemon] Error al iniciar subproceso:', err);
  process.exit(1);
});

child.on('exit', (code, signal) => {
  console.log(`[Bridge Daemon] Proceso ContpaqiBridge terminó con código ${code}, señal ${signal}`);
  process.exit(code ?? 1);
});

process.on('SIGINT', () => {
  child.kill('SIGINT');
  process.exit(0);
});

process.on('SIGTERM', () => {
  child.kill('SIGTERM');
  process.exit(0);
});
