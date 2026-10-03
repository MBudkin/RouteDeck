@echo off
chcp 65001 > nul
cd /d "%~dp0"
echo Сборка RouteDeck.exe...
py -3 -m pip install -q -r requirements.txt pyinstaller pillow || goto :error
py -3 tools\make_icon.py || goto :error
py -3 tools\make_version_info.py || goto :error
py -3 -m PyInstaller --noconfirm --clean --onefile --windowed ^
    --name RouteDeck ^
    --icon static\icon.ico ^
    --version-file build\version_info.txt ^
    --add-data "templates;templates" ^
    --add-data "static;static" ^
    --collect-all webview ^
    desktop.py || goto :error
echo.
echo Готово: dist\RouteDeck.exe
pause
exit /b 0

:error
echo Сборка не удалась
pause
exit /b 1
