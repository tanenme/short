@echo off
title Doksli Finder AI - Exa Local
cd /d "E:\YT\short\_sistem\doksli-web"
echo ======================================================
echo    Doksli Finder AI (Exa Neural Video Search)
echo ======================================================
echo  Memeriksa dependensi...
python -m pip install -q -r requirements.txt 2>nul
echo  Server berjalan di: http://localhost:8000
echo  Membuka browser dalam 2 detik...
echo ======================================================
timeout /t 2 /nobreak >nul
start "" http://localhost:8000
python server.py
pause
