@echo off
setlocal
title Lector - Parar Servidor
cd /d "%~dp0"

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0parar_servidor.ps1"
exit /b %ERRORLEVEL%
