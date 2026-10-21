# Deploy AI Teammates control plane to credit-funded GCE e2-micro (no GCS required).
# Usage: .\scripts\gcp\deploy-control-plane.ps1

$ErrorActionPreference = "Stop"
$Root = "E:\opendot"
$Gcloud = Join-Path $env:LOCALAPPDATA "Google\Cloud SDK\google-cloud-sdk\bin\gcloud.cmd"
$CredDir = Join-Path $env:USERPROFILE ".ai-teammates\gcp"
$Cred = Join-Path $CredDir "ai-teamates-20d66983b27b.json"
if (-not (Test-Path $Cred)) {
    $alt = Get-ChildItem (Join-Path $CredDir "*.json") -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($alt) { $Cred = $alt.FullName }
}
if (-not (Test-Path $Cred)) { throw "Missing service account JSON under ~/.ai-teammates/gcp/" }
if (-not (Test-Path $Gcloud)) { throw "gcloud not found" }

$Project = "ai-teamates"
$Zone = "us-central1-a"
$Instance = "aitm-control-plane"
$SaEmail = "ai-teammates-compute@ai-teamates.iam.gserviceaccount.com"
$RepoUrl = "https://github.com/priyanshiiitr/opendot.git"
$RepoBranch = "m10-cloud-computers"

Write-Host "Activating service account..."
& $Gcloud auth activate-service-account --key-file="$Cred" 2>$null | Out-Null
& $Gcloud config set project $Project 2>$null | Out-Null

# Base64 of SA JSON for metadata (single-line)
$SaB64 = [Convert]::ToBase64String([IO.File]::ReadAllBytes($Cred))

$Startup = Join-Path $Root "scripts\gcp\control-plane-startup.sh"
$startupText = [IO.File]::ReadAllText($Startup) -replace "`r`n", "`n"
$StartupLf = Join-Path $env:TEMP "aitm-startup.sh"
[IO.File]::WriteAllText($StartupLf, $startupText)

Write-Host "Ensuring firewall rule allow-aitm-api..."
$ErrorActionPreference = "Continue"
$fw = & $Gcloud compute firewall-rules describe allow-aitm-api --format="value(name)" 2>$null
$ErrorActionPreference = "Stop"
if (-not $fw) {
    & $Gcloud compute firewall-rules create allow-aitm-api --allow=tcp:8000 --target-tags=aitm-control --description="AI Teammates control plane API demo"
}

# Write metadata file for large values
$MetaFile = Join-Path $env:TEMP "aitm-metadata.txt"
@(
    "aitm-repo-url=$RepoUrl"
    "aitm-repo-branch=$RepoBranch"
    "aitm-sa-b64=$SaB64"
) | Set-Content -Path $MetaFile -Encoding ascii

$ErrorActionPreference = "Continue"
$exists = & $Gcloud compute instances describe $Instance --zone=$Zone --format="value(name)" 2>$null
$ErrorActionPreference = "Stop"

if ($exists) {
    Write-Host "Instance exists - updating metadata and resetting..."
    $SaMeta = Join-Path $env:TEMP "aitm-sa-b64.txt"
    Set-Content -Path $SaMeta -Value $SaB64 -Encoding ascii -NoNewline
    & $Gcloud compute instances add-metadata $Instance --zone=$Zone --metadata="aitm-repo-url=$RepoUrl,aitm-repo-branch=$RepoBranch"
    & $Gcloud compute instances add-metadata $Instance --zone=$Zone --metadata-from-file="startup-script=$StartupLf,aitm-sa-b64=$SaMeta"
    & $Gcloud compute instances reset $Instance --zone=$Zone
} else {
    Write-Host "Creating control-plane VM $Instance ..."
    $SaMeta = Join-Path $env:TEMP "aitm-sa-b64.txt"
    Set-Content -Path $SaMeta -Value $SaB64 -Encoding ascii -NoNewline
    & $Gcloud compute instances create $Instance `
        --project=$Project `
        --zone=$Zone `
        --machine-type=e2-micro `
        --image-family=debian-12 `
        --image-project=debian-cloud `
        --boot-disk-size=30GB `
        --boot-disk-type=pd-balanced `
        --tags=aitm-control `
        --service-account=$SaEmail `
        --scopes=https://www.googleapis.com/auth/cloud-platform `
        --labels=ai-teammates=control-plane,role=control-plane `
        --metadata="aitm-repo-url=$RepoUrl,aitm-repo-branch=$RepoBranch" `
        --metadata-from-file="startup-script=$StartupLf,aitm-sa-b64=$SaMeta"
}

Write-Host "Waiting for external IP..."
$ip = $null
for ($i = 0; $i -lt 36; $i++) {
    $ErrorActionPreference = "Continue"
    $ip = & $Gcloud compute instances describe $Instance --zone=$Zone --format="value(networkInterfaces[0].accessConfigs[0].natIP)" 2>$null
    $ErrorActionPreference = "Stop"
    if ($ip) { break }
    Start-Sleep -Seconds 5
}
if (-not $ip) { throw "No external IP on control plane VM" }
$PublicUrl = "http://${ip}:8000"
Write-Host "Public URL: $PublicUrl"
& $Gcloud compute instances add-metadata $Instance --zone=$Zone --metadata="aitm-public-url=$PublicUrl" | Out-Null

Write-Host "Waiting for /health (git clone + pip + playwright can take 10-15 min on e2-micro)..."
$healthy = $false
for ($i = 0; $i -lt 90; $i++) {
    try {
        $resp = Invoke-WebRequest -Uri ($PublicUrl + "/health") -UseBasicParsing -TimeoutSec 5
        if ($resp.StatusCode -eq 200) {
            Write-Host ("HEALTH OK: " + $resp.Content)
            $healthy = $true
            break
        }
    } catch {
        Write-Host ("  attempt " + ($i + 1) + "/90 not ready yet...")
    }
    Start-Sleep -Seconds 10
}
if (-not $healthy) {
    Write-Warning "Health check did not succeed yet. Tail of serial log:"
    $ErrorActionPreference = "Continue"
    & $Gcloud compute instances get-serial-port-output $Instance --zone=$Zone 2>&1 | Select-Object -Last 60
    throw "Control plane did not become healthy in time"
}

$EnvFile = Join-Path $Root "backend\.env"
if (Test-Path $EnvFile) {
    $lines = Get-Content $EnvFile
    $found = $false
    $out = foreach ($line in $lines) {
        if ($line -match '^AI_TEAMMATES_GCP_CONTROL_PLANE_URL=') {
            $found = $true
            "AI_TEAMMATES_GCP_CONTROL_PLANE_URL=$PublicUrl"
        } else { $line }
    }
    if (-not $found) { $out += "AI_TEAMMATES_GCP_CONTROL_PLANE_URL=$PublicUrl" }
    Set-Content -Path $EnvFile -Value $out -Encoding utf8
    Write-Host "Updated local backend/.env CONTROL_PLANE_URL"
}

Write-Host ""
Write-Host "Control plane deployed."
Write-Host ("  URL:      " + $PublicUrl)
Write-Host ("  Instance: " + $Instance + " (" + $Zone + ")")
Write-Host "  Cost:     e2-micro + 30GB disk while running (credit-funded)."
Write-Host ("  Stop:     gcloud compute instances stop " + $Instance + " --zone=" + $Zone)
Write-Host ("  Delete:   gcloud compute instances delete " + $Instance + " --zone=" + $Zone + " --quiet")
