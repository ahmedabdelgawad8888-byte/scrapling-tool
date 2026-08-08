@echo off
setlocal

REM scrapling-tool quick-launch
REM - Sets up the venv on first run (uv sync, including dev group)
REM - Installs Playwright + Chromium on first run
REM - Launches the complete scraping, discovery, and lookalike dashboard
REM
REM Usage: scrapling-tool.bat [serve|cli|install|test]
REM   serve  (default) - start the web UI on http://127.0.0.1:8080
REM   cli              - drop into a venv-activated shell with `scrape` on PATH
REM   install          - install Playwright browsers (Chromium) only
REM   test             - run the test suite
REM
REM Optional env vars:
REM   SCRAPLING_PORT  - port for the web UI (default 8080)
REM   SCRAPLING_HOST  - host for the web UI (default 127.0.0.1)
REM   SCRAPLING_BROWSER - 1 to auto-open the browser after start (default 1)

set "PROJECT_DIR=%~dp0"
set "VENV=%PROJECT_DIR%.venv"
set "VENV_PY=%VENV%\Scripts\python.exe"
set "VENV_SCRAPE=%VENV%\Scripts\scrape.exe"
set "VENV_SERVE=%VENV%\Scripts\scraper-serve.exe"
set "PORT=%SCRAPLING_PORT%"
if "%PORT%"=="" set "PORT=8080"
set "HOST=%SCRAPLING_HOST%"
if "%HOST%"=="" set "HOST=127.0.0.1"
set "OPEN_BROWSER=%SCRAPLING_BROWSER%"
if "%OPEN_BROWSER%"=="" set "OPEN_BROWSER=1"

REM --- find uv -----------------------------------------------------------------
where uv >nul 2>&1
if errorlevel 1 (
    echo [scrapling-tool] ERROR: 'uv' is not on PATH.
    echo Install it from https://docs.astral.sh/uv/ then re-run.
    exit /b 1
)

REM --- one-time venv setup ------------------------------------------------------
if not exist "%VENV_PY%" (
    echo [scrapling-tool] Creating venv at "%VENV%"...
    uv venv --python 3.14 "%VENV%"
    if errorlevel 1 exit /b 1
)

REM --- sync deps on every launch (uv is fast, this is a no-op when locked) -----
echo [scrapling-tool] Syncing dependencies...
pushd "%PROJECT_DIR%"
uv sync --group dev
if errorlevel 1 (
    popd
    exit /b 1
)

REM --- dispatch ----------------------------------------------------------------
set "CMD=%~1"
if "%CMD%"=="" set "CMD=serve"

if /i "%CMD%"=="serve" goto :serve
if /i "%CMD%"=="cli"   goto :cli
if /i "%CMD%"=="install" goto :install
if /i "%CMD%"=="test"  goto :test
if /i "%CMD%"=="help"  goto :help
if /i "%CMD%"=="/?"    goto :help

echo [scrapling-tool] Unknown command: %CMD%
goto :help

REM ============================ subcommands ====================================

:serve
echo [scrapling-tool] Starting web UI on http://%HOST%:%PORT%/

REM ensure a browser is available for the scrapling fetchers
"%VENV_PY%" -c "import playwright" >nul 2>&1
if errorlevel 1 (
    echo [scrapling-tool] Installing Playwright browser...
    "%VENV_PY%" -m playwright install chromium
)

REM open the browser once the server is up
if "%OPEN_BROWSER%"=="1" start "" cmd /c "timeout /t 3 /nobreak >nul 2>&1 & start "" http://%HOST%:%PORT%/"

"%VENV_SERVE%" --host %HOST% --port %PORT%
popd
endlocal
goto :eof

:cli
echo [scrapling-tool] Dropping into an activated shell. Type 'scrape --help' to start.
"%VENV_SCRAPE%" --help
"%VENV_PY%" -i
popd
endlocal
goto :eof

:install
echo [scrapling-tool] Installing Playwright browser...
"%VENV_PY%" -m playwright install chromium
popd
endlocal
goto :eof

:test
echo [scrapling-tool] Running test suite...
"%VENV_PY%" -m pytest tests\scrapling_tool -v
popd
endlocal
goto :eof

:help
echo scrapling-tool.bat - quick launch
echo.
echo Usage: scrapling-tool.bat [serve^|cli^|install^|test^|help]
echo   serve   (default) - start the web UI on http://127.0.0.1:8080
echo   cli               - launch a Python REPL with the venv active
echo   install           - install Playwright + Chromium
echo   test              - run the pytest suite
echo.
echo Env vars: SCRAPLING_PORT, SCRAPLING_HOST, SCRAPLING_BROWSER
popd
endlocal
goto :eof
