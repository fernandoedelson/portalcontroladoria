@echo off
title Popular banco - Portal de Consolidacao
cd /d "%~dp0"

echo.
echo  ATENCAO: isto APAGA todos os dados do portal e recria do zero
echo  (envios das empresas, atividades do time, indicadores, carteira).
echo.

set /p RESP="  Digite APAGAR para confirmar (ou Enter para cancelar): "
if /i not "%RESP%"=="APAGAR" goto :cancelado

"C:\Scripts\WPy64-31700\python\python.exe" seed.py

echo.
echo  Pronto. Agora rode: iniciar_portal.bat
echo.
pause
exit /b 0

:cancelado
echo.
echo  Cancelado. Nada foi alterado.
echo.
pause
