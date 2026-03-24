@echo off
setlocal EnableExtensions

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo Creating virtual environment...
    py -m venv ".venv"
    if errorlevel 1 goto :error
)

call ".venv\Scripts\activate.bat"
if errorlevel 1 goto :error

echo Installing dependencies...
python -m pip install -r requirements.txt
if errorlevel 1 goto :error

echo Checking pywitpaescraper dependency...
if not exist "%~dp0deps\pywitpaescraper\pywitpaescraper.py" (
    echo Initializing pywitpaescraper submodule...
    git submodule update --init --recursive
    if errorlevel 1 (
        echo Git submodule init failed; attempting direct clone...
        git clone https://github.com/drwilliamroney/pywitpaescraper "%~dp0deps\pywitpaescraper"
        if errorlevel 1 goto :error
    )
)

echo Updating pywitpaescraper to tracked branch...
git submodule update --init --recursive --remote --merge deps\pywitpaescraper
if errorlevel 1 (
    echo Failed to update pywitpaescraper submodule from remote branch.
    goto :error
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
