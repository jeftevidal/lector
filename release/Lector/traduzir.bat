@echo off
setlocal
cd /d "%~dp0"
set "INPUT=%~1"
if not defined INPUT set /p "INPUT=Digite ou arraste o caminho do arquivo EPUB e pressione Enter: "
if not exist "%INPUT%" (
  echo ERRO: nao encontrei esse arquivo. Arraste um arquivo .epub para traduzir.bat.
  pause
  exit /b 1
)
if not exist "%~dp0.venv\Scripts\python.exe" (
  echo ERRO: o programa ainda nao foi instalado. Execute instalar.bat primeiro.
  pause
  exit /b 1
)
echo Abrindo o assistente interativo de traducao.
"%~dp0.venv\Scripts\python.exe" -m epub_translate.cli "%INPUT%" --interactive --glossary "%~dp0glossary.json"
if errorlevel 1 (
  echo ERRO: a traducao nao foi concluida. Consulte o relatorio ao lado do EPUB.
  pause
  exit /b 1
)
for %%I in ("%INPUT%") do explorer "%%~dpI"
pause
exit /b 0
