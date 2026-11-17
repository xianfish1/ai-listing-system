param(
  [int]$Port = 8787,
  [string]$HostName = "",
  [string]$StorageBackend = "",
  [string]$DatabaseUrl = "",
  [string]$CdpUrl = "http://127.0.0.1:9223",
  [switch]$NoAutomationWorker
)

$ErrorActionPreference = "Stop"
$env:API_PORT = "$Port"
if ($HostName) {
  $env:API_HOST = $HostName
}
if ($StorageBackend) {
  $env:STORAGE_BACKEND = $StorageBackend
}
if ($DatabaseUrl) {
  $env:DATABASE_URL = $DatabaseUrl
}

$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $projectRoot
# 部署初始化兜底：系统没有 node 时自动下载安装（已装则幂等秒过）。
# 新装的 node 只写注册表里的系统 PATH，当前进程不会自动刷新——从注册表重读一次。
python scripts\install_node.py
if ($LASTEXITCODE -eq 0) {
  $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
  $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
  $env:Path = "$machinePath;$userPath"
}
# Worker lifecycle is owned by the API supervisor and configured in Settings.
# Keep legacy parameters accepted so existing shortcuts continue to start the API.
python -m services.api.main
