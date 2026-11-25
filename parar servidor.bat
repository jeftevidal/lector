@echo off
setlocal
title Lector - Parar Servidor
cd /d "%~dp0"

set "TARGET_DIR="
if exist "%~dp0epub-translate-0.2.0\parar_servidor.ps1" (
  set "TARGET_DIR=%~dp0epub-translate-0.2.0"
) else (
  for /d %%D in ("%~dp0epub-translate-*") do (
    if exist "%%~fD\parar_servidor.ps1" set "TARGET_DIR=%%~fD"
  )
)

if not defined TARGET_DIR (
  echo ERRO: Nao foi possivel localizar a pasta do projeto epub-translate.
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%TARGET_DIR%\parar_servidor.ps1"
exit /b %ERRORLEVEL%
