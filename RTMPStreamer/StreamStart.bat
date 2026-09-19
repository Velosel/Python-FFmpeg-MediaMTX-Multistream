@echo off
setlocal EnableExtensions

title Ravaelv Multistream Launcher

echo.
echo ==========================================
echo       Ravaelv Multistream Launcher
echo ==========================================
echo.

REM ------------------------------------------------------------
REM Project layout. Every path is derived from the location of
REM this file, so the project folder can live anywhere.
REM
REM   PROJECT\
REM       FFmpeg\          optional bundled FFmpeg
REM                        containing bin\ffmpeg.exe or ffmpeg.exe
REM       MediaMTX\        mediamtx.exe and mediamtx.yml
REM       RTMPStreamer\    StreamStart.bat   - this file
REM                        MultistreamApp.py
REM                        chat_aggregator_auto_refresh_youtube_auto.py
REM                        chat_config.json
REM                        .venv\
REM ------------------------------------------------------------
set "APP_DIR=%~dp0"
if "%APP_DIR:~-1%"=="\" set "APP_DIR=%APP_DIR:~0,-1%"

for %%I in ("%APP_DIR%\..") do set "ROOT_DIR=%%~fI"

set "MEDIAMTX_DIR=%ROOT_DIR%\MediaMTX"
set "FFMPEG_DIR=%ROOT_DIR%\FFmpeg"

set "VENV_PYTHON=%APP_DIR%\.venv\Scripts\python.exe"
set "MEDIAMTX_EXE=%MEDIAMTX_DIR%\mediamtx.exe"
set "STREAM_APP=%APP_DIR%\MultistreamApp.py"
set "CHAT_APP=%APP_DIR%\chat_aggregator_auto_refresh_youtube_auto.py"
set "CHAT_CONFIG=%APP_DIR%\chat_config.json"

REM ------------------------------------------------------------
REM Validate required files
REM ------------------------------------------------------------
call :require "MediaMTX" "%MEDIAMTX_EXE%" || exit /b 1
call :require "Python virtual environment" "%VENV_PYTHON%" || exit /b 1
call :require "Multistream application" "%STREAM_APP%" || exit /b 1
call :require "Chat aggregator" "%CHAT_APP%" || exit /b 1
call :require "Chat config file" "%CHAT_CONFIG%" || exit /b 1

REM ------------------------------------------------------------
REM FFmpeg: prefer the bundled copy, otherwise use PATH.
REM The new windows started below inherit this PATH.
REM ------------------------------------------------------------
set "FFMPEG_BIN="
if exist "%FFMPEG_DIR%\bin\ffmpeg.exe" set "FFMPEG_BIN=%FFMPEG_DIR%\bin"
if not defined FFMPEG_BIN if exist "%FFMPEG_DIR%\ffmpeg.exe" set "FFMPEG_BIN=%FFMPEG_DIR%"
if defined FFMPEG_BIN set "PATH=%FFMPEG_BIN%;%PATH%"

where ffmpeg >nul 2>&1
if errorlevel 1 goto :ffmpeg_missing

set "FFMPEG_SOURCE=PATH"
if defined FFMPEG_BIN set "FFMPEG_SOURCE=%FFMPEG_BIN%"

echo Project root : %ROOT_DIR%
echo App folder   : %APP_DIR%
echo MediaMTX     : %MEDIAMTX_DIR%
echo FFmpeg       : %FFMPEG_SOURCE%
echo.

REM ------------------------------------------------------------
REM 1. Start MediaMTX
REM ------------------------------------------------------------
echo [1/3] Starting MediaMTX...
start "MediaMTX" /D "%MEDIAMTX_DIR%" cmd /k "mediamtx.exe"

timeout /t 3 /nobreak >nul

REM ------------------------------------------------------------
REM 2. Start Python multistream application
REM ------------------------------------------------------------
echo [2/3] Starting MultistreamApp...
start "MultistreamApp" /D "%APP_DIR%" cmd /k ".venv\Scripts\python.exe MultistreamApp.py"

timeout /t 3 /nobreak >nul

REM ------------------------------------------------------------
REM 3. Start chat aggregator + native overlay
REM ------------------------------------------------------------
echo [3/3] Starting Chat Aggregator...
start "Chat Aggregator" /D "%APP_DIR%" cmd /k ".venv\Scripts\python.exe chat_aggregator_auto_refresh_youtube_auto.py"

echo.
echo ==========================================
echo All components have been started.
echo ==========================================
echo.
echo MediaMTX       : running
echo MultistreamApp : running
echo Chat Aggregator: running
echo.
echo You can close this launcher window.
echo The three application windows will remain open.
echo.

endlocal
exit /b 0

REM ------------------------------------------------------------
REM Subroutines
REM ------------------------------------------------------------
:require
if exist "%~2" exit /b 0
echo.
echo [ERROR] %~1 not found:
echo         %~2
echo.
pause
exit /b 1

:ffmpeg_missing
echo.
echo [ERROR] ffmpeg.exe was not found. Looked in:
echo         %FFMPEG_DIR%\bin
echo         %FFMPEG_DIR%
echo         and on PATH
echo.
pause
exit /b 1
