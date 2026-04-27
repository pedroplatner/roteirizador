@echo off
cd /d "%~dp0"

:: Mata qualquer processo que esteja usando a porta 8502
for /f "tokens=5" %%a in ('netstat -aon 2^>nul ^| findstr ":8502 "') do (
    taskkill /F /PID %%a >nul 2>&1
)

:: Aguarda a porta liberar
timeout /t 1 /nobreak >nul

:: Abre o navegador
start "" http://localhost:8502

:: Inicia o servidor
python server.py
