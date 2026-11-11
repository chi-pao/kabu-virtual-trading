@echo off
cd /d "%~dp0"
echo ===== %date% %time% ===== >> "仮想売買\実行ログ.txt"
py paper.py >> "仮想売買\実行ログ.txt" 2>&1
start "" "仮想売買\成績.html"
