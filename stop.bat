@echo off
cd /d "%~dp0"
docker compose down
echo Story Forge stopped. Your worlds are kept.
timeout /t 3 >nul
