@echo off
chcp 65001 >nul
title QUANT RADAR (Jiankong) - 全资产即时监控终端
echo ========================================================
echo   ⚡ QUANT RADAR - A股/港股/指数/ETF 全资产实时监控系统
echo   包含分类: 主要基准指数 + 2年热点高波动ETF + 题材龙头 + Terminal基本盘
echo   服务地址: http://127.0.0.1:8765
echo ========================================================
echo 正在打开默认浏览器...
start "" "http://127.0.0.1:8765"
echo.
echo 正在启动高频行情、巨潮信披与7x24快讯监听服务...
cd /d "%~dp0"
python scripts/web_server.py
pause
