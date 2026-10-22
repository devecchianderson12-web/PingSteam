@echo off
rem Compila el .exe localmente (requiere Python instalado)
pip install pyinstaller
pyinstaller --onefile --noconsole --name SteamBestServer steam_best_server.py
echo.
echo Listo: dist\SteamBestServer.exe
pause
