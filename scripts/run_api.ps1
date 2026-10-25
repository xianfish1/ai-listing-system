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
# Worker lifecycle is owned by the API supervisor and configured in Settings.
# Keep legacy parameters accepted so existing shortcuts continue to start the API.
python -m services.api.main
