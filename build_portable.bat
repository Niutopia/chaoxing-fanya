@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion

echo ╔══════════════════════════════════════════════════════════════╗
echo ║         超星学习通自动化工具 - 便携版打包工具               ║
echo ╚══════════════════════════════════════════════════════════════╝
echo.

set "SCRIPT_DIR=%~dp0"
set "BUILD_DIR=%SCRIPT_DIR%portable_build"
set "DIST_DIR=%SCRIPT_DIR%chaoxing_portable"
set "PYTHON_VERSION=3.13.7"
set "PYTHON_EMBED_URL=https://www.python.org/ftp/python/%PYTHON_VERSION%/python-%PYTHON_VERSION%-embed-amd64.zip"
set "PIP_BOOTSTRAP_VERSION=26.2.1"
set "PIP_BOOTSTRAP_URL=https://bootstrap.pypa.io/pip/zipapp/pip-%PIP_BOOTSTRAP_VERSION%.pyz"

echo [1/7] 清理旧的构建目录...
if exist "%BUILD_DIR%" rd /s /q "%BUILD_DIR%"
if exist "%DIST_DIR%" rd /s /q "%DIST_DIR%"
mkdir "%BUILD_DIR%"
mkdir "%DIST_DIR%"

echo [2/7] 下载嵌入式 Python %PYTHON_VERSION%...
set "PYTHON_ZIP=%BUILD_DIR%\python-embed.zip"
set "PYTHON_DIR=%DIST_DIR%\python"

powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PYTHON_EMBED_URL%' -OutFile '%PYTHON_ZIP%'}" 2>nul
if errorlevel 1 (
    echo    ❌ 下载 Python 失败，请检查网络连接
    echo    您也可以手动下载: %PYTHON_EMBED_URL%
    pause
    exit /b 1
)
echo    ✅ Python 下载完成

echo [3/7] 解压 Python 运行时...
mkdir "%PYTHON_DIR%"
powershell -Command "Expand-Archive -Path '%PYTHON_ZIP%' -DestinationPath '%PYTHON_DIR%' -Force"
if errorlevel 1 (
    echo    ❌ 解压失败
    pause
    exit /b 1
)

REM 验证嵌入包确实是 Python 3.13
set "PTH_FILE=%PYTHON_DIR%\python313._pth"
if not exist "%PTH_FILE%" (
    echo    ❌ 嵌入包缺少 python313._pth，停止打包
    pause
    exit /b 1
)
echo python313.zip> "%PTH_FILE%"
echo .>> "%PTH_FILE%"
echo Lib>> "%PTH_FILE%"
echo Lib\site-packages>> "%PTH_FILE%"
echo import site>> "%PTH_FILE%"
echo    ✅ Python 解压完成

echo [4/7] 安装项目依赖...
set "PYTHON_EXE=%PYTHON_DIR%\python.exe"
set "PIP_BOOTSTRAP=%BUILD_DIR%\pip.pyz"

REM Download a version-pinned pip zipapp; the official embeddable runtime has no pip.
powershell -Command "& {[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; Invoke-WebRequest -Uri '%PIP_BOOTSTRAP_URL%' -OutFile '%PIP_BOOTSTRAP%'}" 2>nul
if errorlevel 1 (
    echo    ❌ 固定版本 pip bootstrap 下载失败，停止打包
    exit /b 1
)
if not exist "%PIP_BOOTSTRAP%" (
    echo    ❌ 未生成固定版本 pip bootstrap，停止打包
    exit /b 1
)

REM requirements.txt contains the exact project dependency pins.
echo    正在安装项目依赖，这可能需要几分钟...
"%PYTHON_EXE%" "%PIP_BOOTSTRAP%" install --disable-pip-version-check --no-warn-script-location -r "%SCRIPT_DIR%requirements.txt"
if errorlevel 1 (
    echo    ❌ 项目依赖安装失败，停止打包
    pause
    exit /b 1
)

echo    ✅ 依赖安装完成
echo    便携版不包含本地 OCR（PaddleOCR、paddlepaddle、paddlex）
echo    验证码请手动输入或配置在线 OCR 服务

echo [5/7] 构建前端...
cd /d "%SCRIPT_DIR%web"
where npm >nul 2>&1
if errorlevel 1 (
    echo    ❌ 未找到 npm，无法按 package-lock.json 构建前端
    exit /b 1
)
echo    按 package-lock.json 安装前端依赖...
call npm ci
if errorlevel 1 (
    echo    ❌ npm ci 失败，停止打包
    exit /b 1
)
echo    正在构建前端...
call npm run build
if errorlevel 1 (
    echo    ❌ 前端构建失败，停止打包
    exit /b 1
)
if not exist "dist\index.html" (
    echo    ❌ 前端构建未生成 dist\index.html
    exit /b 1
)
xcopy /E /I /Y "dist" "%DIST_DIR%\web\dist" >nul
if errorlevel 1 (
    echo    ❌ 前端文件复制失败，停止打包
    exit /b 1
)
echo    ✅ 前端构建完成
cd /d "%SCRIPT_DIR%"

echo [6/7] 复制项目文件...
REM 复制 Python 源码
xcopy /E /I /Y "%SCRIPT_DIR%api" "%DIST_DIR%\api" >nul
xcopy /E /I /Y "%SCRIPT_DIR%resource" "%DIST_DIR%\resource" >nul

REM 复制主要文件
copy "%SCRIPT_DIR%app.py" "%DIST_DIR%\" >nul
copy "%SCRIPT_DIR%main.py" "%DIST_DIR%\" >nul
copy "%SCRIPT_DIR%requirements.txt" "%DIST_DIR%\" >nul

REM 只复制不含凭据的配置模板，绝不复制实际配置文件
if exist "%SCRIPT_DIR%config.ini.example" copy "%SCRIPT_DIR%config.ini.example" "%DIST_DIR%\config.ini.example" >nul
echo    不会打包 web_config.json，Web 设置请在 Web 界面中配置

echo    ✅ 文件复制完成

echo [7/7] 创建启动脚本...

REM 创建便携版启动脚本
(
echo @echo off
echo chcp 65001 ^>nul 2^>^&1
echo setlocal enabledelayedexpansion
echo.
echo set "SCRIPT_DIR=%%~dp0"
echo set "PYTHON_EXE=%%SCRIPT_DIR%%python\python.exe"
echo set "CHAOXING_ENABLE_OCR=0"
echo.
echo pushd "%%SCRIPT_DIR%%"
echo.
echo echo.
echo echo ========================================================
echo echo           超星学习通自动化工具 - 便携版
echo echo ========================================================
echo echo.
echo.
echo if exist "web\dist\index.html" ^(
echo     echo [INFO] 检测到前端构建，将启动 Web 模式...
echo     echo [INFO] 正在打开浏览器: http://localhost:5000
echo     echo.
echo     start "" cmd /c "ping -n 3 127.0.0.1 ^>nul ^&^& start http://localhost:5000"
echo     "%%PYTHON_EXE%%" app.py
echo ^) else ^(
echo     echo [INFO] 未检测到前端，将启动命令行模式...
echo     echo.
echo     "%%PYTHON_EXE%%" main.py
echo ^)
echo.
echo popd
echo pause
) > "%DIST_DIR%\启动.bat"

REM 创建命令行版启动脚本
(
echo @echo off
echo chcp 65001 ^>nul 2^>^&1
echo setlocal
echo.
echo set "SCRIPT_DIR=%%~dp0"
echo set "PYTHON_EXE=%%SCRIPT_DIR%%python\python.exe"
echo set "CHAOXING_ENABLE_OCR=0"
echo.
echo pushd "%%SCRIPT_DIR%%"
echo.
echo echo.
echo echo ========================================================
echo echo         超星学习通自动化工具 - 命令行模式
echo echo ========================================================
echo echo.
echo "%%PYTHON_EXE%%" main.py
echo.
echo popd
echo pause
) > "%DIST_DIR%\命令行启动.bat"

REM 创建 Web 版启动脚本
(
echo @echo off
echo chcp 65001 ^>nul 2^>^&1
echo setlocal
echo.
echo set "SCRIPT_DIR=%%~dp0"
echo set "PYTHON_EXE=%%SCRIPT_DIR%%python\python.exe"
echo set "CHAOXING_ENABLE_OCR=0"
echo.
echo pushd "%%SCRIPT_DIR%%"
echo.
echo echo.
echo echo ========================================================
echo echo           超星学习通自动化工具 - Web 模式
echo echo ========================================================
echo echo.
echo echo [INFO] 正在打开浏览器: http://localhost:5000
echo echo.
echo start "" cmd /c "ping -n 3 127.0.0.1 ^>nul ^&^& start http://localhost:5000"
echo "%%PYTHON_EXE%%" app.py
echo.
echo popd
echo pause
) > "%DIST_DIR%\Web启动.bat"

REM 创建 README
(
echo # 超星学习通自动化工具 - 便携版
echo.
echo ## 使用方法
echo.
echo 1. **双击 `启动.bat`** - 自动选择合适的启动模式
echo 2. **双击 `Web启动.bat`** - 启动 Web 界面模式，在浏览器打开 http://localhost:5000
echo 3. **双击 `命令行启动.bat`** - 启动命令行模式
echo.
echo ## 配置说明
echo.
echo - 将 `config.ini.example` 复制为 `config.ini`，再编辑账号密码和学习参数
echo - Web 模式设置请在 Web 界面中配置（不会打包 web_config.json）
echo.
echo ## 注意事项
echo.
echo - 首次运行可能需要较长时间初始化程序
echo - 请确保网络连接正常
echo - 本便携版不包含本地 OCR（PaddleOCR、paddlepaddle、paddlex），请手动输入验证码或配置在线 OCR 服务
echo - 如遇问题，请查看控制台输出的错误信息
echo.
echo ## 目录结构
echo.
echo ```
echo chaoxing_portable/
echo ├── python/          # 嵌入式 Python 运行时
echo ├── api/             # 后端 API 模块
echo ├── web/dist/        # 前端静态文件
echo ├── resource/        # 资源文件
echo ├── 启动.bat         # 主启动脚本
echo ├── Web启动.bat      # Web 模式启动
echo └── 命令行启动.bat   # 命令行模式启动
echo ```
) > "%DIST_DIR%\README.md"

echo    ✅ 启动脚本创建完成

REM 清理临时文件
rd /s /q "%BUILD_DIR%" 2>nul

echo.
echo ╔══════════════════════════════════════════════════════════════╗
echo ║                      打包完成！                              ║
echo ╠══════════════════════════════════════════════════════════════╣
echo ║  输出目录: chaoxing_portable                                 ║
echo ║                                                              ║
echo ║  使用方法:                                                   ║
echo ║    1. 将 chaoxing_portable 文件夹复制到任意位置              ║
echo ║    2. 双击 "启动.bat" 运行程序                               ║
echo ╚══════════════════════════════════════════════════════════════╝
echo.

REM 询问是否打包为 zip
set /p CREATE_ZIP="是否将便携版打包为 ZIP 文件? (Y/N): "
if /i "%CREATE_ZIP%"=="Y" (
    echo 正在创建 ZIP 文件...
    powershell -Command "Compress-Archive -Path '%DIST_DIR%\*' -DestinationPath '%SCRIPT_DIR%chaoxing_portable.zip' -Force"
    if errorlevel 1 (
        echo    ❌ ZIP 创建失败
    ) else (
        echo    ✅ 已创建: chaoxing_portable.zip
    )
)

echo.
pause
