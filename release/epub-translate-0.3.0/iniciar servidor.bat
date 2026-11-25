@echo off
setlocal
title Lector - Servidor Web
cd /d "%~dp0"

echo ===================================================
echo   Lector - Tradutor de EPUB (Servidor Web)
echo ===================================================

set "VENV_DIR=%~dp0.venv"
set "VENV_PYTHON=%VENV_DIR%\Scripts\python.exe"

if exist "%VENV_PYTHON%" goto check_deps

where python >nul 2>nul
if errorlevel 1 (
  echo ERRO: Python nao foi encontrado no sistema.
  echo Instale Python 3.11 ou superior e adicione-o ao PATH.
  echo https://www.python.org/downloads/
  pause
  exit /b 1
)

python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
if errorlevel 1 (
  echo ERRO: O Lector requer Python 3.11 ou mais recente.
  python --version
  pause
  exit /b 1
)

echo [Lector] Criando ambiente virtual local (.venv)...
python -m venv "%VENV_DIR%"
if errorlevel 1 (
  echo ERRO: Falha ao criar o ambiente virtual em "%VENV_DIR%".
  pause
  exit /b 1
)

:check_deps
echo [Lector] Verificando dependencias...
"%VENV_PYTHON%" -c "import fastapi, uvicorn, lxml, rich, questionary, multipart, epub_translate" >nul 2>nul
if not errorlevel 1 goto deps_ok

echo [Lector] Dependencias incompletas ou ausentes. Instalando agora...
"%VENV_PYTHON%" -m pip install -e ".[dev]"
if errorlevel 1 (
  echo [Lector] Tentando instalacao padrao sem dependencias de teste...
  "%VENV_PYTHON%" -m pip install -e .
)

"%VENV_PYTHON%" -c "import fastapi, uvicorn, lxml, rich, questionary, multipart, epub_translate" >nul 2>nul
if errorlevel 1 (
  echo ERRO: Nao foi possivel instalar todas as dependencias necessarias.
  echo Verifique sua conexao com a internet e tente novamente.
  pause
  exit /b 1
)
echo [Lector] Dependencias instaladas com sucesso.

:deps_ok
echo [Lector] Dependencias verificadas com sucesso.

set "FOUND_CLI="
for %%C in (claude codex agy ollama) do (
  where %%C >nul 2>nul
  if not errorlevel 1 set "FOUND_CLI=1"
)
if defined FOUND_CLI goto start_server
echo [Aviso] Nenhum CLI claude, codex, agy ou ollama encontrado no PATH.
echo [Aviso] Voce podera simular traducoes ou configurar os executaveis pela interface.

:start_server
echo [Lector] Iniciando servidor web em http://127.0.0.1:8788 ...
"%VENV_PYTHON%" -m epub_translate.web %*
if not errorlevel 1 goto end
echo [Lector] O servidor encerrou com erro (codigo %ERRORLEVEL%).
pause

:end
exit /b 0
