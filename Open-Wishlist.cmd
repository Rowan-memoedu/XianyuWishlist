@echo off
pwsh.exe -NoLogo -NoProfile -File "%~dp0Start-Wishlist.ps1"
if errorlevel 1 pause
