@echo off
rem Inicio rapido de RECEP-SALUD IA: doble clic. Enciende la base de datos y la aplicacion y abre el panel.
rem Opciones: iniciar.cmd -SinNavegador   |   iniciar.cmd -Puerto 8010
title RECEP-SALUD IA - iniciar
powershell -NoProfile -File "%~dp0scripts\iniciar.ps1" %*
if errorlevel 1 (
  echo.
  echo Algo fallo. Lee el mensaje de arriba.
  pause
  exit /b 1
)
ping -n 7 127.0.0.1 >nul
