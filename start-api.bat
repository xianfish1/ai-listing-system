@echo off
title AI Listing System API (port 8788)
cd /d "%~dp0"
set API_PORT=8788
curl -s -m 2 -o nul http://127.0.0.1:8788/api/health >nul 2>&1
if not errorlevel 1 (
  echo 8788 端口上已经有服务在运行，无需重复启动。
  echo 如需重启：先关掉已有服务的窗口/进程，再运行本脚本。
  echo.
  pause
  exit /b 0
)
echo 启动 AI Listing System API: http://127.0.0.1:8788
echo 关闭此窗口即停止服务。
echo.
python -m services.api.main
echo.
echo 服务已退出（退出码 %ERRORLEVEL%）。
pause
