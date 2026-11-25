@echo off
setlocal
cd /d "%~dp0"

set "TARGET_DIR="
if exist "%~dp0epub-translate-0.2.0\iniciar servidor.bat" (
  set "TARGET_DIR=%~dp0epub-translate-0.2.0"
) else (
  for /d %%D in ("%~dp0epub-translate-*") do (
    if exist "%%~fD\iniciar servidor.bat" set "TARGET_DIR=%%~fD"
  )
)

if not defined TARGET_DIR (
  echo ERRO: Nao foi possivel localizar a pasta do projeto epub-translate.
  pause
  exit /b 1
)

cd /d "%TARGET_DIR%"
call ".\iniciar servidor.bat" %*
