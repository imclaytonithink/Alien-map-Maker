@echo off
REM === Build a standalone .exe for Sci-Fi Battlemap Builder (run on Windows) ===
REM 1) Install Python 3.10+ from python.org, then open a terminal in this folder.
REM 2) Run this file (double-click) OR paste the commands below.

python -m venv venv
call venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
pip install pyinstaller

REM Build a single-file executable (no console window).
pyinstaller --noconsole --onefile --name "BattlemapBuilder" main.py

echo.
echo Done. Your executable is in the dist\ folder: dist\BattlemapBuilder.exe
pause
