@echo off
chcp 65001 >nul
cd /d "%~dp0"
start "ERA5 转 Excel 服务" /D "%~dp0" cmd /k python app.py
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:5000"
