@echo off
rem ============================================================
rem  TPV Moderno - instalacion del servidor en Windows
rem  Haz clic derecho aqui y elige "Ejecutar como administrador"
rem ============================================================
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
pause
