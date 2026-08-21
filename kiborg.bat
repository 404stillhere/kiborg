@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion

set "KIBORG_DIR=M:\projects\kiborg"
set "PYTHON=M:\projects\funpay\venv\Scripts\python.exe"
set "PYTHON_TESTS=M:\projects\darbot\venv\Scripts\python.exe"

cd /d "%KIBORG_DIR%"

:menu
cls
echo.
echo  === KIBORG ===
echo.
echo  1. Panel      (http://127.0.0.1:8737)
echo  2. Ideas      (run once)
echo  3. Oracle     (plan for project and goal)
echo  4. Tests      (run_tests.py)
echo  5. Open folder
echo  0. Exit
echo.
set /p choice="Choice: "

if "%choice%"=="1" goto launch_panel
if "%choice%"=="2" goto launch_ideas
if "%choice%"=="3" goto launch_oracle
if "%choice%"=="4" goto launch_tests
if "%choice%"=="5" goto launch_openfolder
if "%choice%"=="0" goto finish

goto menu

:launch_panel
echo Starting panel...
start "" "%PYTHON%" "%KIBORG_DIR%\panel\serve.py"
timeout /t 2 >nul
start http://127.0.0.1:8737
goto menu

:launch_ideas
echo Run: bring fresh ideas...
"%PYTHON%" "%KIBORG_DIR%\cyborg\run.py"
pause
goto menu

:launch_oracle
echo.
set /p project="Project path: "
set /p goal="Oracle goal: "
"%PYTHON%" "%KIBORG_DIR%\cyborg\run.py" --mode oracle --project "%project%" --goal "%goal%"
pause
goto menu

:launch_tests
echo Running tests...
"%PYTHON_TESTS%" "%KIBORG_DIR%\run_tests.py"
pause
goto menu

:launch_openfolder
start "" "%KIBORG_DIR%"
goto menu

:finish
endlocal
