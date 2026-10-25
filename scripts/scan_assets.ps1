param(
  [Parameter(Mandatory=$true)]
  [string]$Root,
  [string]$Out = "data\asset-scan.json"
)

$ErrorActionPreference = "Stop"
python -m workers.asset_scanner.cli --root $Root --out $Out
