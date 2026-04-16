@echo off
cd /d "%~dp0"
echo Iniciando Roteirizador...
start "" http://localhost:8501
streamlit run grok2.py --server.headless true
