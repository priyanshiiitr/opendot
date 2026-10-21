# OPTIONAL: register a CURRENT-USER Startup shortcut for the backend service.
# Does NOT run as SYSTEM, does NOT require admin, does NOT install a Windows Service.
# You must run this script yourself; the app never silently changes startup settings.
#
#   .\scripts\register-user-startup.ps1
#   .\scripts\register-user-startup.ps1 -Remove

param([switch]$Remove)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Startup = [Environment]::GetFolderPath("Startup")
$ShortcutPath = Join-Path $Startup "AITeammates-Backend.lnk"
$Target = Join-Path $Root "scripts\start-backend.ps1"

if ($Remove) {
    if (Test-Path $ShortcutPath) { Remove-Item $ShortcutPath -Force }
    Write-Host "Removed user Startup shortcut (if present): $ShortcutPath"
    exit 0
}

$Wsh = New-Object -ComObject WScript.Shell
$Sc = $Wsh.CreateShortcut($ShortcutPath)
$Sc.TargetPath = "powershell.exe"
$Sc.Arguments = "-NoProfile -WindowStyle Minimized -ExecutionPolicy Bypass -File `"$Target`""
$Sc.WorkingDirectory = $Root
$Sc.Description = "AI Teammates local backend (user session only)"
$Sc.Save()
Write-Host "Created user Startup shortcut: $ShortcutPath"
Write-Host "Disable anytime with: .\scripts\register-user-startup.ps1 -Remove"
