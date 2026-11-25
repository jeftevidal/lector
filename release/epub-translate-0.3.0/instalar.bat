@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
  echo ERRO: Python nao foi encontrado. Instale Python 3.11 ou mais recente.
  echo Marque a opcao para adicionar Python ao PATH durante a instalacao.
  pause
  exit /b 1
)
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)"
if errorlevel 1 (
  echo ERRO: esta ferramenta precisa do Python 3.11 ou mais recente.
  python --version
  pause
  exit /b 1
)
echo Python encontrado:
python --version

set "CLI_FOUND="
for %%C in (claude codex agy ollama) do (
  where %%C >nul 2>nul
  if not errorlevel 1 set "CLI_FOUND=1"
)
if not defined CLI_FOUND (
  echo AVISO: nenhum CLI suportado foi encontrado no PATH.
  echo Instale um destes provedores: claude, codex, agy ou ollama.
) else (
  echo CLIs disponiveis:
  for %%C in (claude codex agy ollama) do (
    where %%C >nul 2>nul
    if not errorlevel 1 echo   %%C
  )
)

if not exist ".venv\Scripts\python.exe" (
  echo Criando ambiente local...
  python -m venv .venv
  if errorlevel 1 goto install_error
)
echo Instalando o programa, dependencias e ferramentas de teste...
.venv\Scripts\python.exe -m pip install --upgrade pip
if errorlevel 1 goto install_error
.venv\Scripts\python.exe -m pip install -e ".[dev]"
if errorlevel 1 goto install_error
echo Instalacao concluida. Abra lector.bat para a interface web ou traduzir.bat para o assistente CLI.
pause
exit /b 0

:install_error
echo ERRO: a instalacao falhou. Verifique a internet e tente novamente.
pause
exit /b 1
