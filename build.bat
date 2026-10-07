@echo off
setlocal
REM === Build a standalone .exe for SceneBoard (run on Windows) ===
REM 1. Install Python 3.10+ from python.org (tick "Add Python to PATH").
REM 2. Install curl (included with current Windows 10/11) and run this file.
REM The high-resolution pack ZIPs total about 239 MB compressed. Unsupported
REM non-image files are filtered out before the supported assets are bundled.
REM The app installs the image-only packs into AppData on first launch.

cd /d "%~dp0"
if errorlevel 1 goto build_error

python -m venv venv
if errorlevel 1 goto build_error
call venv\Scripts\activate
python -m pip install --upgrade pip
if errorlevel 1 goto build_error
pip install -r requirements.txt
if errorlevel 1 goto build_error
pip install pyinstaller
if errorlevel 1 goto build_error

REM Cache the public release archives outside the repository. The same release
REM is used by .github/workflows/tests.yml for real-pack integration checks.
set "PACK_DIR=%TEMP%\SceneBoard-highres-packs"
set "FILTERED_PACK_DIR=%TEMP%\SceneBoard-supported-asset-packs"
set "PACK_BASE=https://github.com/imclaytonithink/Alien-map-Maker/releases/download/asset-intake-temp-20261006"
if not exist "%PACK_DIR%" mkdir "%PACK_DIR%"
if errorlevel 1 goto build_error

call :fetch_pack "RPG-Mobius-Geomorphs-Geomorphs-High-Res-Teal.zip"
if errorlevel 1 goto pack_error
call :fetch_pack "RPG-Mobius-Geomorphs-Custom-Tiles-High-Res-Teal.zip"
if errorlevel 1 goto pack_error
call :fetch_pack "RPG-Mobius-Geomorphs-Symbols-High-Res-Teal.zip"
if errorlevel 1 goto pack_error

REM Strip documentation and other unsupported files before bundling. The
REM filtered archives keep their folder layout but contain supported images only.
python filter_asset_packs.py "%PACK_DIR%" "%FILTERED_PACK_DIR%"
if errorlevel 1 goto build_error

REM Bundle the image-only high-resolution packs into the EXE.
pyinstaller --noconsole --onefile --clean --noconfirm --name "SceneBoard" --add-data "%FILTERED_PACK_DIR%;asset_packs" main.py
if errorlevel 1 goto build_error

echo.
echo Done. Your executable is in dist\SceneBoard.exe.
echo The built-in high-resolution packs install into the user's AppData store
 echo automatically the first time the app runs.
goto finish

:fetch_pack
set "PACK_PATH=%PACK_DIR%\%~1"
if exist "%PACK_PATH%" (
    python -c "import sys,zipfile; sys.exit(0 if zipfile.is_zipfile(sys.argv[1]) else 1)" "%PACK_PATH%"
    if not errorlevel 1 exit /b 0
    del "%PACK_PATH%" >nul 2>&1
)
echo Downloading %~1 ...
curl.exe --fail --location --retry 3 --output "%PACK_PATH%" "%PACK_BASE%/%~1"
if errorlevel 1 exit /b 1
python -c "import sys,zipfile; sys.exit(0 if zipfile.is_zipfile(sys.argv[1]) else 1)" "%PACK_PATH%"
if errorlevel 1 (
    del "%PACK_PATH%" >nul 2>&1
    exit /b 1
)
exit /b 0

:pack_error
echo.
echo ERROR: Could not download or validate a high-resolution asset pack.
echo Check your internet connection and try building again.
goto finish_error

:build_error
echo.
echo ERROR: Build step failed. Review the message above.
goto finish_error

:finish_error
set "RESULT=1"
goto cleanup

:finish
set "RESULT=0"

:cleanup
if exist venv\Scripts\deactivate.bat call venv\Scripts\deactivate.bat
pause
exit /b %RESULT%
