@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto usepython
py -3 server.py --port 8766 --duo --browser
goto done
:usepython
python server.py --port 8766 --duo --browser
:done
if errorlevel 1 pause
