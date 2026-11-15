@echo off
setlocal
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
  py -3 -m venv .venv
  if errorlevel 1 goto missingpython
  ".venv\Scripts\python.exe" -m pip install -r requirements-lock.txt
  if errorlevel 1 goto failed
)
".venv\Scripts\pythonw.exe" Start.pyw
exit /b
:missingpython
echo Install Python 3.11 or newer from python.org, then run this file again.
echo For the easiest setup, download the Windows x64 ZIP from Releases instead.
pause
exit /b 1
:failed
echo Setup did not finish. Check your internet connection and try again.
pause
exit /b 1
