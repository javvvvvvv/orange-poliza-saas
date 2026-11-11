@echo off
REM ============================================================================
REM PROPIEDAD INTELECTUAL Y LICENCIA COMERCIAL CERRADA - ORANGE CREW
REM ============================================================================
cd /d "%~dp0contpaqi-bridge"
if not exist appsettings.json (
    echo Falta contpaqi-bridge\appsettings.json
    echo Copia appsettings.example.json a appsettings.json y pon tu usuario/contrasena de CONTPAQi.
    pause
    exit /b 1
)

set DOTNET_X86="C:\Program Files (x86)\dotnet\dotnet.exe"
if not exist %DOTNET_X86% (
    set DOTNET_X86=dotnet
)

echo Iniciando ContpaqiBridge con motor x86 y auto-recuperacion...
:bucle_servicio
%DOTNET_X86% "%~dp0contpaqi-bridge\bin\Debug\net10.0\ContpaqiBridge.dll" --urls "http://0.0.0.0:5005"

echo.
echo [%date% %time%] ContpaqiBridge se detuvo o fue cerrado.
echo Reiniciando automaticamente en 3 segundos (Auto-recuperacion activa)...
timeout /t 3 /nobreak >nul
goto bucle_servicio
