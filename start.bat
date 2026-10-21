@echo off
rem Starts Story Forge using this folder for settings (.env). Same as launcher\StoryForge.bat.
set "STORYFORGE_HOME=%~dp0."
call "%~dp0launcher\StoryForge.bat" %*
