@echo off
rem aila: terminal client. Put this folder on PATH, then run "aila" in any project folder.
setlocal
set "PYTHONPATH=%~dp0;%PYTHONPATH%"
"%~dp0.venv\Scripts\python.exe" -X utf8 -m app.cli %*
exit /b %ERRORLEVEL%
