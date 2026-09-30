@echo off
chcp 65001 >nul
title QUANT RADAR (Jiankong) - GitHub 仓库一键同步工具
color 0b

echo ========================================================
echo   🚀 QUANT RADAR (Jiankong) - GitHub 远程仓库同步工具
echo   目标仓库: https://github.com/vegayan2024/jiankong.git
echo   目标分支: main
echo ========================================================
echo.

cd /d "%~dp0"

echo [1/4] 正在检查本地代码变更状态 (git status)...
echo --------------------------------------------------------
git status -s
echo --------------------------------------------------------
echo.

echo 请确认是否将上述变更提交并推送到 GitHub 远程仓库？
echo 按任意键立即开始同步，或直接关闭此窗口取消...
pause >nul
echo.

echo [2/4] 正在暂存所有变更文件 (git add .)...
git add .
echo [OK] 暂存完成。
echo.

echo [3/4] 正在记录提交日志 (git commit)...
git commit -m "feat: 升级AIHOT行业情报中枢，接入行业官方微信公众号矩阵，民爆固定第4位，板块数量实时统计"
if %ERRORLEVEL% NEQ 0 (
    echo [提示] 没有检测到新改动，或者所有改动已提交。
) else (
    echo [OK] 提交完成。
)
echo.

echo [4/4] 正在推送到 GitHub (git push origin main)...
git push origin main
if %ERRORLEVEL% EQU 0 (
    echo.
    echo ========================================================
    echo   🎉 恭喜！代码已成功同步推送到 GitHub 远程仓库！
    echo   访问地址: https://github.com/vegayan2024/jiankong
    echo ========================================================
) else (
    echo.
    echo ========================================================
    echo   ⚠️ 推送遇到问题，正在尝试拉取远程更新并合并 (git pull --rebase)...
    echo ========================================================
    git pull --rebase origin main
    echo 正在重新尝试推送...
    git push origin main
    if %ERRORLEVEL% EQU 0 (
        echo [OK] 重新推送成功！
    ) else (
        echo [ERROR] 推送失败，请检查网络连通性或 GitHub 账号权限。
    )
)

echo.
pause
