@echo off
setlocal
title Auto Research Windows Internal Test
set "AUTORESEARCH_SCRIPT=%~dp0build_windows.ps1"
if not exist "%AUTORESEARCH_SCRIPT%" set "AUTORESEARCH_SCRIPT=%~dp0Auto-Research-Windows-Source\desktop\windows\build_windows.ps1"
if not exist "%AUTORESEARCH_SCRIPT%" (
  echo Frozen source folder is incomplete. Please wait for OneDrive to finish downloading.
  pause
  exit /b 2
)
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%AUTORESEARCH_SCRIPT%"
set "AUTORESEARCH_EXIT=%ERRORLEVEL%"
echo.
if not "%AUTORESEARCH_EXIT%"=="0" echo The check did not pass. Please keep Windows-Build-Report.txt.
if "%AUTORESEARCH_EXIT%"=="0" echo The build kit check passed. Setup has NOT been generated in this preview kit.
echo.
pause
exit /b %AUTORESEARCH_EXIT%
