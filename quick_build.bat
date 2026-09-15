@echo off
chcp 65001 >nul
setlocal

echo ╔══════════════════════════════════════════════════════════════╗
echo ║       超星学习通自动化工具 - 快速打包（仅构建前端）          ║
echo ╚══════════════════════════════════════════════════════════════╝
echo.

set "SCRIPT_DIR=%~dp0"
set "WEB_DIR=%SCRIPT_DIR%web"

echo [1/2] 检查 Node.js 环境...
where npm >nul 2>&1
if errorlevel 1 (
    echo    ❌ 未找到 npm，请先安装 Node.js
    echo    下载地址: https://nodejs.org/
    pause
    exit /b 1
)
echo    ✅ Node.js 已安装

echo [2/2] 构建前端...
cd /d "%WEB_DIR%"

echo    按 package-lock.json 安装依赖...
call npm ci
if errorlevel 1 (
    echo    ❌ npm ci 失败，停止构建
    exit /b 1
)

echo    正在构建...
call npm run build
if errorlevel 1 (
    echo    ❌ 构建失败，停止构建
    exit /b 1
)
if not exist "dist\index.html" (
    echo    ❌ 构建未生成 dist\index.html
    exit /b 1
)

cd /d "%SCRIPT_DIR%"

echo.
echo ╔══════════════════════════════════════════════════════════════╗
echo ║                      构建完成！                              ║
echo ╠══════════════════════════════════════════════════════════════╣
echo ║  前端已构建到: web/dist                                      ║
echo ║                                                              ║
echo ║  现在可以:                                                   ║
echo ║    1. 运行 start.bat 启动程序                                ║
echo ║    2. 运行 build_portable.bat 创建完整便携版                 ║
echo ╚══════════════════════════════════════════════════════════════╝
echo.
pause
