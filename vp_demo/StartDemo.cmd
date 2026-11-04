@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto usepython
py -3 server.py --port 8765 --browser
goto done
:usepython
python server.py --port 8765 --browser
:done
if errorlevel 1 pause
