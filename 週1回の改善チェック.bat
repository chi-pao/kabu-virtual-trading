@echo off
cd /d "%~dp0"
echo 作戦の比べっこをしています（数分かかります）…
py improve.py
start "" "結果\改善レポート.html"
pause
