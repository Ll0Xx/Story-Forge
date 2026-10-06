@echo off
setlocal
cd /d "%~dp0"
title Story Forge

where docker >nul 2>&1
if errorlevel 1 (
  echo Docker is not installed.
  echo Install Docker Desktop from https://www.docker.com/products/docker-desktop/ and run this again.
  pause
  exit /b 1
)

docker info >nul 2>&1
if not errorlevel 1 goto docker_ready
echo Starting Docker Desktop...
start "" "%ProgramFiles%\Docker\Docker\Docker Desktop.exe"
set /a tries=0
:wait_docker
timeout /t 3 /nobreak >nul
docker info >nul 2>&1
if not errorlevel 1 goto docker_ready
set /a tries+=1
if %tries% lss 60 goto wait_docker
echo Docker did not start. Open Docker Desktop manually and run this again.
pause
exit /b 1

:docker_ready
echo Checking for updates...
docker compose pull --quiet >nul 2>&1
echo Starting Story Forge (the first start can take a few minutes)...
docker compose up -d
if errorlevel 1 (
  echo Failed to start Story Forge.
  pause
  exit /b 1
)

set /a tries=0
:wait_app
curl -s -o nul http://localhost:8000/api/health
if not errorlevel 1 goto app_ready
set /a tries+=1
if %tries% geq 60 goto app_ready
timeout /t 1 /nobreak >nul
goto wait_app

:app_ready
start "" http://localhost:8000
echo Story Forge is running at http://localhost:8000
echo It keeps running in the background. To stop it, run stop.bat.
timeout /t 5 >nul
