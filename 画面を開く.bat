@echo off
cd /d "%~dp0"
title 仮想売買ボード（この画面を閉じると止まります）
git pull -q
py dashboard.py
