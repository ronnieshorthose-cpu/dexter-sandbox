@echo off
rem Dexter launcher for Windows. Double-click this file to open Dexter's guided menu.
rem It only runs Dexter from this folder with the Python already installed on this
rem computer. It does not download, install or change anything, and it never uses
rem the network. Dexter keeps its records in the .dexter folder in your user folder,
rem outside this repository.
setlocal EnableExtensions
title Dexter - local project coordinator
cd /d "%~dp0"
set "PYTHONUTF8=1"
set "PYTHONIOENCODING=utf-8"

if not exist "dexter_coordinator\__main__.py" goto :no_files

rem Find Python 3.9 or newer: first the "py" launcher from python.org, then "python".
set "DEXTER_PY="
py -3 -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
if not errorlevel 1 set "DEXTER_PY=py -3"
if defined DEXTER_PY goto :found_python
python -c "import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)" >nul 2>nul
if not errorlevel 1 set "DEXTER_PY=python"
if defined DEXTER_PY goto :found_python
goto :no_python

:found_python
rem Dexter protects the owner passphrase with hashlib.scrypt, part of normal Python builds.
%DEXTER_PY% -c "import hashlib; hashlib.scrypt" >nul 2>nul
if errorlevel 1 goto :no_scrypt

echo Starting Dexter...
%DEXTER_PY% -m dexter_coordinator %* start
set "DEXTER_EXIT=%ERRORLEVEL%"
if "%DEXTER_EXIT%"=="0" goto :done
echo.
echo  Dexter stopped with a problem, error code %DEXTER_EXIT%.
echo  Nothing was lost: Dexter saves each change completely or not at all.
echo  Read the message above. If it mentions the database, open Dexter again and
echo  choose C, Check records and back up.
goto :fail

:no_files
echo.
echo  Dexter could not find its program files next to this launcher.
echo  Keep dexter.bat inside the dexter-sandbox folder, next to the folder named
echo  dexter_coordinator, and start it from there.
echo  A desktop shortcut to dexter.bat is fine; a copy of the file on its own is not.
goto :fail

:no_python
echo.
echo  Python 3.9 or newer was not found on this computer. Dexter needs it.
echo    1. Download Python from https://www.python.org/downloads/
echo    2. Run the installer. On its first screen, tick "Add python.exe to PATH".
echo    3. Close this window and double-click dexter.bat again.
echo  If the Microsoft Store opens instead, close it and use the python.org installer.
goto :fail

:no_scrypt
echo.
echo  This Python installation is missing a security feature Dexter needs: hashlib.scrypt.
echo  Please install Python from https://www.python.org/downloads/ and try again.
goto :fail

:fail
echo.
echo  For step-by-step help, open docs\GETTING-STARTED-WINDOWS.md in this folder.
if not defined DEXTER_NO_PAUSE pause
endlocal
exit /b 1

:done
if not defined DEXTER_NO_PAUSE pause
endlocal
exit /b 0
