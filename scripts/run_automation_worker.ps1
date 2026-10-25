param(
  [string]$ApiUrl = "http://127.0.0.1:8787",
  [string]$CdpUrl = "http://127.0.0.1:9223",
  [double]$PollSeconds = 3,
  [int]$LeaseSeconds = 120,
  [switch]$Once
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $projectRoot

$workerArgs = @(
  "-m", "scripts.automation_worker",
  "--api-url", $ApiUrl,
  "--cdp-url", $CdpUrl,
  "--poll-seconds", "$PollSeconds",
  "--lease-seconds", "$LeaseSeconds"
)
if ($Once) {
  $workerArgs += "--once"
}

python @workerArgs
exit $LASTEXITCODE
