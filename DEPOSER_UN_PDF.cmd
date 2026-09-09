@echo off
setlocal
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\windows\Deposer_PDF_MEMS_Forge.ps1"
if errorlevel 1 (
  echo.
  echo Une erreur a empeche l'envoi du PDF.
  echo Consulte le message ci-dessus ou ouvre GitHub Desktop pour verifier la connexion.
  echo.
  pause
)
endlocal
