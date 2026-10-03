@echo off
chcp 65001 > nul
cd /d "%~dp0"
title RouteDeck
echo ====================================
echo  RouteDeck
echo  http://localhost:5000
echo  Для остановки закройте окно или нажмите CTRL+C
echo ====================================
echo.
start "" /b cmd /c "timeout /t 2 /nobreak > nul & start http://localhost:5000"
py -3 app.py
pause
