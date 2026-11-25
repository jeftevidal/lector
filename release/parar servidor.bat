@echo off
setlocal
cd /d "%~dp0"

set "TARGET_DIR="
if exist "%~dp0Lector\parar_servidor.ps1" (
  set "TARGET_DIR=%~dp0Lector"
) else if exist "%~dp0Lector\parar servidor.bat" (
  set "TARGET_DIR=%~dp0Lector"
) else if exist "%~dp0epub-translate-0.3.0\parar_servidor.ps1" (
  set "TARGET_DIR=%~dp0epub-translate-0.3.0"
) else (
  for /d %%D in ("%~dp0epub-translate-*") do (
    if exist "%%~fD\parar_servidor.ps1" set "TARGET_DIR=%%~fD"
  )
)

if not defined TARGET_DIR (
  echo ERRO: Nao foi possivel localizar a pasta do projeto Lector.
  pause
  exit /b 1
)

cd /d "%TARGET_DIR%"
powershell -NoProfile -ExecutionPolicy Bypass -File "%TARGET_DIR%\parar_servidor.ps1"
exit /b %ERRORLEVEL%

