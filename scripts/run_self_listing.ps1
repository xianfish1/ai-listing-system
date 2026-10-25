param(
  [Parameter(Mandatory = $true)]
  [string]$JobId,

  [ValidateSet("health", "audit", "fill")]
  [string]$Mode = "audit"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $ProjectRoot

$env:BU_CDP_URL = "http://127.0.0.1:9223"

python scripts\self_run_listing.py --job-id $JobId --mode $Mode
exit $LASTEXITCODE
