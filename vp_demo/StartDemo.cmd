@echo off
cd /d "%~dp0"
where py >nul 2>nul
if errorlevel 1 goto usepython
py -3 server.py --browser
goto done
:usepython
python server.py --browser
:done
if errorlevel 1 pause
