@echo off
title Controladoria J^&F S.A.
cd /d "%~dp0"

echo.
echo   CONTROLADORIA J^&F S.A.
echo   Abrindo no navegador: http://127.0.0.1:5050
echo   Para parar: feche esta janela ou pressione Ctrl+C.
echo.

REM Abre o navegador ~3s depois (tempo do servidor subir), sem travar o servidor.
start "" /min cmd /c "timeout /t 3 >nul & start "" http://127.0.0.1:5050"

"C:\Scripts\WPy64-31700\python\python.exe" run.py

echo.
echo   Servidor encerrado.
pause
