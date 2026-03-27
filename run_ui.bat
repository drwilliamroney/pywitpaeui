@echo off
setlocal EnableExtensions

cd /d "%~dp0"

rem Check that a 64-bit Python 3 is available via the Python Launcher.
py -3-64 --version >nul 2>&1
if errorlevel 1 (
    echo.
    echo ERROR: 64-bit Python 3 was not found.
    echo The UI requires 64-bit Python 3 to run.
    echo To install it, run the following command in a terminal:
    echo.
    echo   winget install --id Python.Python.3 --architecture x64 --source winget
    echo.
    echo After installation, restart this script.
    pause
    exit /b 1
)

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -3-64 -m venv ".venv"
    if errorlevel 1 goto :error
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 goto :error

echo Installing dependencies...
python -m pip install --upgrade pip
if errorlevel 1 goto :error
python -m pip install -r requirements.txt
if errorlevel 1 goto :error

set "SCRAPER_DEP_PATH=%~dp0deps\pywitpaescraper"
set "SCRAPER_REPO_URL=https://github.com/drwilliamroney/pywitpaescraper"

echo Ensuring pywitpaescraper dependency is present...
if not exist "%~dp0deps" mkdir "%~dp0deps"

if exist "%SCRAPER_DEP_PATH%\.git" (
    echo Updating pywitpaescraper from origin/main...
    git -C "%SCRAPER_DEP_PATH%" fetch origin main
    if errorlevel 1 goto :error
    git -C "%SCRAPER_DEP_PATH%" checkout main
    if errorlevel 1 goto :error
    git -C "%SCRAPER_DEP_PATH%" pull --ff-only origin main
    if errorlevel 1 goto :error
) else (
    if exist "%SCRAPER_DEP_PATH%" (
        echo Existing non-git directory found at "%SCRAPER_DEP_PATH%".
        echo Please remove or rename it, then run again.
        goto :error
    )
    echo Cloning pywitpaescraper...
    git clone --branch main --single-branch "%SCRAPER_REPO_URL%" "%SCRAPER_DEP_PATH%"
    if errorlevel 1 goto :error
)

echo Bootstrapping pywitpaescraper runtime...
call "%~dp0deps\pywitpaescraper\bootstrap_scraper.bat"
if errorlevel 1 (
    echo Failed to bootstrap pywitpaescraper runtime.
    goto :error
)

set "DEFAULT_SIDE=allies"
set "DEFAULT_GAME_PATH=C:\Matrix Games\War in the Pacific Admiral's Edition"

set "RUN_SIDE="
set /p "RUN_SIDE=Run as [allies/japan] (default: %DEFAULT_SIDE%): "
if "%RUN_SIDE%"=="" set "RUN_SIDE=%DEFAULT_SIDE%"

if /I not "%RUN_SIDE%"=="allies" if /I not "%RUN_SIDE%"=="japan" (
    echo Invalid side supplied. Falling back to %DEFAULT_SIDE%.
    set "RUN_SIDE=%DEFAULT_SIDE%"
)

set "GAME_PATH="
set /p "GAME_PATH=Game save directory path (default: %DEFAULT_GAME_PATH%): "
if "%GAME_PATH%"=="" set "GAME_PATH=%DEFAULT_GAME_PATH%"

set "APP_SIDE=%RUN_SIDE%"
set "APP_GAME_PATH=%GAME_PATH%"
set "APP_PWSTOOL_PATH=%~dp0deps\pywitpaescraper"

echo Starting web UI on http://127.0.0.1:8080/
start "" "http://127.0.0.1:8080/"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8080 --reload
exit /b %errorlevel%

:error
echo Failed to start UI.
exit /b 1
