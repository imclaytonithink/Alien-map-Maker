@echo off
setlocal
REM === Build SceneBoard for Windows (run on Windows) ===
REM 1. Install Python 3.10+ from python.org (tick "Add Python to PATH").
REM 2. Install curl (included with current Windows 10/11) and run this file.
REM
REM By default this makes a folder build, which starts quickly:
REM     dist\SceneBoard\SceneBoard.exe   - run this; keep the SceneBoard folder together
REM     dist\SceneBoard-Windows.zip      - the same folder, zipped for sharing
REM "build.bat onefile" makes the older single dist\SceneBoard.exe instead. That is
REM one file, but it unpacks everything (about 280 MB) into a temporary folder
REM every time it starts, so it opens more slowly.
REM
REM The high-resolution pack ZIPs total about 239 MB compressed. Unsupported
REM non-image files are filtered out before the supported assets are bundled.
REM The app installs the image-only packs into AppData on first launch.

cd /d "%~dp0"
if errorlevel 1 goto build_error

set "BUILD_MODE=onedir"
if /i "%~1"=="onefile" set "BUILD_MODE=onefile"

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
REM Tag of the GitHub release that holds the asset ZIPs. Keep it in sync with
REM ASSET_RELEASE_TAG in .github/workflows/tests.yml.
set "PACK_TAG=Released"
set "PACK_BASE=https://github.com/imclaytonithink/Alien-map-Maker/releases/download/%PACK_TAG%"
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

set "ICON=ui\icons\SceneBoard.ico"
if not exist "%ICON%" python make_icon.py
if errorlevel 1 goto build_error

REM Data files the program reads at run time: the Geomorph tile list, archetypes,
REM scenarios and symbol kits, and the MU/TH/UR room catalog. Without them the
REM Geomorph Generator and the terminal export cannot start.
set "APP_DATA=--add-data "geomorph\data;geomorph\data" --add-data "core\muthur_catalog.json;core""

if /i "%BUILD_MODE%"=="onefile" goto build_onefile

REM Folder build: the image-only packs sit next to the program, so nothing has
REM to be unpacked when it starts.
if exist "dist\SceneBoard.exe" del /q "dist\SceneBoard.exe"
pyinstaller --noconsole --onedir --clean --noconfirm --name "SceneBoard" --icon "%ICON%" --add-data "%FILTERED_PACK_DIR%;asset_packs" %APP_DATA% main.py
if errorlevel 1 goto build_error
call :make_zip
if errorlevel 1 (
    echo WARNING: Could not create dist\SceneBoard-Windows.zip. The app itself is fine.
)

echo.
echo Done. Your app is in dist\SceneBoard - run SceneBoard.exe inside that folder.
echo Keep the whole SceneBoard folder together; make a shortcut to the EXE if you like.
echo dist\SceneBoard-Windows.zip holds the same folder, ready to share.
echo The built-in high-resolution packs install into the user's AppData store
echo automatically the first time the app runs.
goto finish

:build_onefile
REM One-file build: everything, packs included, inside a single EXE.
if exist "dist\SceneBoard-Windows.zip" del /q "dist\SceneBoard-Windows.zip"
if exist "dist\SceneBoard\" rmdir /s /q "dist\SceneBoard"
pyinstaller --noconsole --onefile --clean --noconfirm --name "SceneBoard" --icon "%ICON%" --add-data "%FILTERED_PACK_DIR%;asset_packs" %APP_DATA% main.py
if errorlevel 1 goto build_error

echo.
echo Done. Your executable is in dist\SceneBoard.exe.
echo The built-in high-resolution packs install into the user's AppData store
echo automatically the first time the app runs.
goto finish

:make_zip
set "ZIP_PATH=dist\SceneBoard-Windows.zip"
if exist "%ZIP_PATH%" del /q "%ZIP_PATH%"
echo Zipping dist\SceneBoard for sharing ...
"%SystemRoot%\System32\tar.exe" -a -c -f "%ZIP_PATH%" -C dist SceneBoard
if not errorlevel 1 exit /b 0
echo tar.exe could not create the zip; trying PowerShell instead ...
if exist "%ZIP_PATH%" del /q "%ZIP_PATH%"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\SceneBoard' -DestinationPath 'dist\SceneBoard-Windows.zip' -Force"
exit /b %errorlevel%

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
echo Expected the release assets at: %PACK_BASE%/
echo Check your internet connection and that release "%PACK_TAG%" still exists
echo and is published, then try building again.
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
