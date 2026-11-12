@echo off
rem Detiene RECEP-SALUD IA: doble clic. Apaga la aplicacion y la base de datos (los datos no se borran).
rem Opcion: detener.cmd -MantenerBD   (deja la base de datos encendida)
title RECEP-SALUD IA - detener
powershell -NoProfile -File "%~dp0scripts\detener.ps1" %*
if errorlevel 1 (
  echo.
  echo Algo fallo. Lee el mensaje de arriba.
  pause
  exit /b 1
)
ping -n 6 127.0.0.1 >nul
