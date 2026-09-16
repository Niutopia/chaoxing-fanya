@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul

echo ========================================
echo   超星学习通 - Web前端启动脚本
echo ========================================
echo.

REM This script follows the checked-in requirements files.  PaddleOCR is an
REM optional local OCR path and is intentionally not installed automatically.
echo [检查] 检查 Python 与 Node.js...
where python >nul 2>&1
if errorlevel 1 (
    echo    ❌ 未找到 Python，请先安装 Python 3.13 或更高版本。
    pause
    exit /b 1
)
where npm >nul 2>&1
if errorlevel 1 (
    echo    ❌ 未找到 npm，请先安装 Node.js 20.19 或更高版本。
    pause
    exit /b 1
)
echo    ✅ Python 与 Node.js 已找到
echo.

echo [1/3] 同步后端依赖...
python -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo    ❌ 后端依赖安装失败！
    pause
    exit /b 1
)
echo    ✅ 后端依赖已与 requirements.txt 同步
echo.

echo [2/3] 同步前端依赖...
pushd "%~dp0web"
call npm ci
set "NPM_EXIT=!errorlevel!"
popd
if not "!NPM_EXIT!"=="0" (
    echo    ❌ 前端依赖安装失败！
    pause
    exit /b !NPM_EXIT!
)
echo    ✅ 前端依赖已与 package-lock.json 同步
echo.

echo [3/3] 启动前后端服务...
start "超星后端服务" cmd /k "cd /d ""%~dp0"" ^&^& python app.py"
timeout /t 3 /nobreak >nul
start "超星前端服务" cmd /k "cd /d ""%~dp0web"" ^&^& npm run dev"

echo.
echo ========================================
echo   服务启动完成！
echo   后端地址: http://localhost:5000
echo   前端地址: http://localhost:3000
echo ========================================
echo.
echo 本脚本不会自动安装 PaddleOCR；如需本地 OCR，请按 README 手动安装并设置 CHAOXING_ENABLE_OCR=1。
echo 请等待浏览器自动打开...
timeout /t 5 /nobreak >nul
start http://localhost:3000

pause
endlocal
