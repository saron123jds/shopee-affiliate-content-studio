@echo off
cd /d %~dp0
python -m venv .venv
call .venv\Scripts\activate
pip install --upgrade pip
pip install -r requirements.txt

set PORT=6001
python app.py
pause
