@echo off
setlocal
pushd "%~dp0"
set "PY=%CD%\.venv\Scripts\python.exe"
set "UV=C:\Users\witek\AppData\Local\Programs\Python\Python314\Scripts\uv.exe"
set "CMD=%~1"

if /I "%CMD%"=="dashboard" goto dashboard
if /I "%CMD%"=="console"   goto console
if /I "%CMD%"=="inspector" goto inspector
if /I "%CMD%"=="web"       goto web
if /I "%CMD%"=="cu"        goto cu
if /I "%CMD%"=="go"        goto go
if /I "%CMD%"=="ask"       goto ask
if /I "%CMD%"=="voice"     goto voice
if /I "%CMD%"=="status"    goto status
if /I "%CMD%"=="stop"      goto stop
if /I "%CMD%"=="help"      goto help
if "%CMD%"==""             goto menu
echo Unknown command: %CMD%
goto help

:menu
cls
echo ==================================================
echo    LAYA  -  local Jev alternative (offline)
echo ==================================================
echo    1  Dashboard      control center + ON/OFF    8769
echo    2  Goal console   type a goal, watch the log  8768
echo    3  Inspector      browser agent decisions     8766
echo    4  Browser use    drive a real Chrome
echo    5  Computer use   drive the desktop
echo    6  Ask Laya       local typed decisions
echo    7  Voice control  speak a goal
echo    8  Status         GPU, model, ports
echo    9  Stop all       GUIs + voice
echo    0  Exit
echo ==================================================
set /p "choice=Choose: "
if "%choice%"=="1" goto dashboard
if "%choice%"=="2" goto console
if "%choice%"=="3" goto inspector
if "%choice%"=="4" goto web
if "%choice%"=="5" goto cu_menu
if "%choice%"=="6" goto ask
if "%choice%"=="7" goto voice
if "%choice%"=="8" goto status
if "%choice%"=="9" goto stop
goto end

:cu_menu
echo.
echo   1  Plan only (dry-run, nothing is touched)
echo   2  Execute (types and clicks)
set /p "mode=Choose: "
if "%mode%"=="2" goto go
goto cu

:dashboard
echo starting control center on http://127.0.0.1:8769 ...
start "Laya Dashboard" "%PY%" "%CD%\laya_dashboard.py"
ping -n 5 127.0.0.1 >nul
start "" "http://127.0.0.1:8769"
goto end

:console
echo starting goal console on http://127.0.0.1:8768 ...
start "Laya Console" "%PY%" "%CD%\laya_console.py"
ping -n 5 127.0.0.1 >nul
start "" "http://127.0.0.1:8768"
goto end

:inspector
echo starting browser agent inspector on http://127.0.0.1:8766 ...
start "Laya Inspector" "%CD%\.venv\Scripts\jev.exe"
ping -n 6 127.0.0.1 >nul
start "" "http://127.0.0.1:8766"
goto end

:web
if "%~2"=="" set /p "goal=Goal (what to do in the browser): "
if not "%~2"=="" set "goal=%~2"
if "%goal%"=="" goto end
"%PY%" "%CD%\examples\run.py" --url "%~3" --goal "%goal%"
goto end

:cu
if "%~2"=="" set /p "goal=Goal (what to do on the desktop): "
if not "%~2"=="" set "goal=%~2"
if "%goal%"=="" goto end
"%PY%" "%CD%\jev_cu.py" --goal "%goal%"
goto end

:go
if "%~2"=="" set /p "goal=Goal (will type and click): "
if not "%~2"=="" set "goal=%~2"
if "%goal%"=="" goto end
"%PY%" "%CD%\jev_cu.py" --goal "%goal%" --go
goto end

:ask
if "%~2"=="" set /p "text=Text to evaluate: "
if not "%~2"=="" set "text=%~2"
if "%text%"=="" goto end
set "preset=%~3"
if "%preset%"=="" set "preset=triage"
"%PY%" "%CD%\laya_ask.py" "%text%" --preset %preset%
goto end

:voice
echo voice control: say a goal, say "stop listening" to quit
"%PY%" "%CD%\laya_voice.py"
goto end

:status
echo.
echo -- engine --
"%PY%" "%CD%\laya_terminal.py" -c "status"
echo -- ports (8769 dashboard, 8768 console, 8766 inspector, 9222 chrome) --
netstat -ano | findstr "LISTENING" | findstr ":8769 :8768 :8766 :9222"
echo.
goto end

:stop
powershell -NoProfile -ExecutionPolicy Bypass -File "%CD%\scripts\laya-stop.ps1"
goto end

:help
echo.
echo   laya                  open the menu
echo   laya dashboard        start the control center (8769)
echo   laya console          start the goal console (8768)
echo   laya inspector        start the browser agent inspector (8766)
echo   laya web "goal"       browser use
echo   laya cu "goal"        computer use, plan only
echo   laya go "goal"        computer use, execute
echo   laya ask "text" [preset]   local decision (triage/email/guard/moderation/router)
echo   laya voice            voice control
echo   laya status           GPU, model, ports
echo   laya stop             stop GUIs + voice
echo.
goto end

:end
if "%CMD%"=="" pause
endlocal
