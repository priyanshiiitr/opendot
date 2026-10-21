# Start the AI Teammates backend (agent service + durable worker) WITHOUT Electron.
# Closing the desktop UI does not stop this process.
# Jobs do NOT run when the PC is powered off.
#
# Usage (from repo root):
#   .\scripts\start-backend.ps1
#   .\scripts\start-backend.ps1 -Reload

param(
    [switch]$Reload
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$VenvPython = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPython)) {
    Write-Error "Missing $VenvPython — create the venv and install backend\requirements.txt first."
}

Set-Location (Join-Path $Root "backend")
$hostBind = if ($env:AI_TEAMMATES_BIND_HOST) { $env:AI_TEAMMATES_BIND_HOST } else { "127.0.0.1" }
$port = if ($env:AI_TEAMMATES_BIND_PORT) { $env:AI_TEAMMATES_BIND_PORT } else { "8000" }

Write-Host "Starting AI Teammates service on http://${hostBind}:${port}"
Write-Host "Health:  http://${hostBind}:${port}/health"
Write-Host "Ready:   http://${hostBind}:${port}/ready"
Write-Host "Remote access stays DISABLED unless AI_TEAMMATES_REMOTE_ACCESS_ENABLED=true"

$argsList = @(
    "-m", "uvicorn", "app.main:app",
    "--host", $hostBind,
    "--port", $port
)
if ($Reload) { $argsList += "--reload" }

& $VenvPython @argsList
