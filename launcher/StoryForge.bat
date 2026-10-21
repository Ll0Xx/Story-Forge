@echo off
rem Story Forge launcher: the only file you need to run Story Forge (plus Docker Desktop).
rem   StoryForge.bat        start Story Forge (and download updates)
rem   StoryForge.bat stop   stop it; your worlds and logins are kept
rem Settings: put a .env file (see .env.example in the repo) into %LOCALAPPDATA%\StoryForge
setlocal
title Story Forge
if not defined STORYFORGE_HOME set "STORYFORGE_HOME=%LOCALAPPDATA%\StoryForge"
set "COMPOSE=%STORYFORGE_HOME%\launcher-compose.yml"
set "URL=http://localhost:8000"
rem Full path: Git for Windows can put a Unix `timeout` first on PATH.
set "WAIT=%SystemRoot%\System32\timeout.exe"

where docker >nul 2>&1
if errorlevel 1 (
  echo Docker is not installed.
  echo Install Docker Desktop from https://www.docker.com/products/docker-desktop/ and run this again.
  pause
  exit /b 1
)

if /i "%~1"=="stop" goto stop

docker info >nul 2>&1
if not errorlevel 1 goto docker_ready
echo Starting Docker Desktop...
start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
set /a tries=0
:wait_docker
"%WAIT%" /t 3 /nobreak >nul
docker info >nul 2>&1
if not errorlevel 1 goto docker_ready
set /a tries+=1
if %tries% lss 60 goto wait_docker
echo Docker did not start. Open Docker Desktop manually and run this again.
pause
exit /b 1

:docker_ready
if not exist "%STORYFORGE_HOME%" mkdir "%STORYFORGE_HOME%"
call :write_compose
echo Checking for updates...
docker compose -f "%COMPOSE%" pull --quiet >nul 2>&1
echo Starting Story Forge (the first start can take a few minutes)...
docker compose -f "%COMPOSE%" up -d --remove-orphans
if errorlevel 1 (
  echo Failed to start Story Forge.
  pause
  exit /b 1
)

set /a tries=0
:wait_app
curl -s -o nul %URL%/api/health
if not errorlevel 1 goto app_ready
set /a tries+=1
if %tries% geq 60 goto app_ready
"%WAIT%" /t 1 /nobreak >nul
goto wait_app

:app_ready
start "" %URL%
echo Story Forge is running at %URL%
echo It keeps running in the background. To stop it, run: StoryForge.bat stop
"%WAIT%" /t 5 >nul
exit /b 0

:stop
docker info >nul 2>&1
if errorlevel 1 (
  echo Docker is not running, so Story Forge is already stopped.
) else (
  docker compose -p storyforge down
  echo Story Forge stopped. Your worlds are kept.
)
"%WAIT%" /t 3 >nul
exit /b 0

rem Same as docker-compose.yml in the repo, minus the local build. Rewritten on every start.
:write_compose
> "%COMPOSE%" (
  echo name: storyforge
  echo services:
  echo   storyforge:
  echo     image: ghcr.io/ll0xx/story-forge:latest
  echo     ports:
  echo       - "127.0.0.1:8000:8000"
  echo     env_file:
  echo       - path: .env
  echo         required: false
  echo     volumes:
  echo       - worlds:/app/worlds
  echo       - claude-home:/home/player/.claude
  echo       - codex-home:/home/player/.codex
  echo     restart: unless-stopped
  echo volumes:
  echo   worlds:
  echo   claude-home:
  echo   codex-home:
)
exit /b 0
