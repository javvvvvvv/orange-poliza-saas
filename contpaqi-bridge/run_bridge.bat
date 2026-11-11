@echo off
cd /d "%~dp0"
"C:\Program Files (x86)\dotnet\dotnet.exe" "%~dp0bin\Debug\net10.0\ContpaqiBridge.dll" --urls "http://0.0.0.0:5005"
