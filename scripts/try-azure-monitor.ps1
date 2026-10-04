# AZURE-MONITOR: sign in to Azure (if needed) and dry-run the Azure Monitor
# readers against the real Log Analytics workspace and managed Prometheus.
#   .\scripts\try-azure-monitor.ps1
param(
    [string]$LogsUrl     = "https://api.loganalytics.io/v1/workspaces/3767efe1-a6c0-437b-9df7-fd64e1b7115a/query",
    [string]$PromUrl     = "https://defaultazuremonitorworkspace-eastus-abcsbnaxa9ewczb5.eastus.prometheus.monitor.azure.com",
    [string]$Namespace   = "default",
    [int]$LookbackMin    = 60,
    [string]$Account     = "2411800@cognizant.com"
)

# Avast HTTPS scanning: Python must trust the Avast root that Windows trusts.
# REQUESTS_CA_BUNDLE covers az / azure-identity, SSL_CERT_FILE covers httpx.
$caBundle = "C:\Users\HP\azcli\ca-bundle-with-avast.pem"
if (Test-Path $caBundle) {
    $env:REQUESTS_CA_BUNDLE = $caBundle
    $env:SSL_CERT_FILE = $caBundle
}
$localAz = "C:\Users\HP\azcli\bin"
if (-not (Get-Command az -ErrorAction SilentlyContinue) -and (Test-Path $localAz)) {
    $env:PATH = "$localAz;$env:PATH"
}

$ErrorActionPreference = "Continue"
# A token pasted into .env (AZURE_ACCESS_TOKEN) skips the sign-in. It is for
# Log Analytics only, so skip Prometheus in that mode.
$envFile = Join-Path (Split-Path $PSScriptRoot -Parent) ".env"
$hasToken = (Test-Path $envFile) -and (Select-String -Path $envFile -Pattern '^\s*(export\s+)?AZURE_ACCESS_TOKEN=\S' -Quiet)
if ($hasToken) {
    Write-Host "Using AZURE_ACCESS_TOKEN from .env (Log Analytics only)." -ForegroundColor Green
    $PromUrl = ""
}
$user = if ($hasToken) { "token from .env" } else { (& az account show --query user.name -o tsv 2>$null) }
if (-not $user) {
    Write-Host "Not signed in to Azure - a browser tab will open. Sign in as $Account." -ForegroundColor Yellow
    & az config set core.enable_broker_on_windows=false --only-show-errors 2>$null | Out-Null
    & az login --output none --only-show-errors
    $user = (& az account show --query user.name -o tsv 2>$null)
    if (-not $user) { Write-Host "Sign-in failed." -ForegroundColor Red; exit 1 }
}
Write-Host "Azure account: $user" -ForegroundColor Green

$root = Split-Path $PSScriptRoot -Parent
$env:PYTHONPATH = $root
$pyArgs = @("--logs-url", $LogsUrl, "--namespace", $Namespace, "--lookback-min", $LookbackMin)
if ($PromUrl) { $pyArgs += @("--prom-url", $PromUrl) }
& python (Join-Path $PSScriptRoot "try_azure_monitor.py") @pyArgs
