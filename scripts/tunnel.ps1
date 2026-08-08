<#
.SYNOPSIS
    Starts the Scrapling dashboard and exposes it through a Cloudflare tunnel.

.DESCRIPTION
    The supported remote-access path for this project. Boots the local server on
    loopback, then puts a Cloudflare quick tunnel in front of it and prints the
    public URL.

    Refuses to open the tunnel when SCRAPLING_PASSWORD is unset: a public URL
    onto an unauthenticated dashboard hands full scraper control to anyone who
    finds it, and quick-tunnel hostnames do get scanned.

.PARAMETER Port
    Local port for the dashboard. Default 8080.

.PARAMETER Force
    Open the tunnel even without a password set. Only for a throwaway demo.

.EXAMPLE
    .\scripts\tunnel.ps1
    .\scripts\tunnel.ps1 -Port 9000
#>
[CmdletBinding()]
param(
    [int]$Port = 8080,
    [switch]$Force
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot

# --- load .env so the password and provider keys are visible to this shell ----
$envFile = Join-Path $root '.env'
if (Test-Path $envFile) {
    Get-Content $envFile | ForEach-Object {
        if ($_ -match '^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$') {
            $name = $Matches[1]
            $value = $Matches[2].Trim().Trim('"').Trim("'")
            if ($value) { Set-Item -Path "Env:$name" -Value $value }
        }
    }
}

# --- refuse to expose an open dashboard ---------------------------------------
if (-not $env:SCRAPLING_PASSWORD -and -not $Force) {
    Write-Host ''
    Write-Host '  SCRAPLING_PASSWORD is not set.' -ForegroundColor Red
    Write-Host '  A Cloudflare quick tunnel is a public URL. Without a password,'
    Write-Host '  anyone who reaches it can run scrapes, read your history and'
    Write-Host '  read your provider keys.'
    Write-Host ''
    Write-Host '  Set one in .env:  SCRAPLING_PASSWORD=something-long'
    Write-Host '  Or re-run with -Force if this is a throwaway demo.'
    Write-Host ''
    exit 1
}

# --- dependencies -------------------------------------------------------------
$cloudflared = (Get-Command cloudflared -ErrorAction SilentlyContinue)?.Source
if (-not $cloudflared) {
    Write-Host '  cloudflared is not on PATH.' -ForegroundColor Red
    Write-Host '  Install it:  winget install --id Cloudflare.cloudflared'
    exit 1
}

$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { $python = 'python' }

# --- boot the dashboard on loopback -------------------------------------------
$env:SCRAPLING_HOST = '127.0.0.1'
$env:SCRAPLING_PORT = "$Port"

Write-Host "  Starting dashboard on http://127.0.0.1:$Port ..." -ForegroundColor Cyan
$server = Start-Process -FilePath $python `
    -ArgumentList '-m', 'webapp.server' `
    -WorkingDirectory $root -PassThru -NoNewWindow

try {
    # Wait for the health endpoint rather than sleeping a fixed amount.
    $ready = $false
    foreach ($attempt in 1..40) {
        Start-Sleep -Milliseconds 500
        try {
            Invoke-WebRequest "http://127.0.0.1:$Port/healthz" -UseBasicParsing -TimeoutSec 2 | Out-Null
            $ready = $true
            break
        } catch { }
    }
    if (-not $ready) { throw "Dashboard did not come up on port $Port." }

    Write-Host '  Dashboard is up. Opening Cloudflare tunnel ...' -ForegroundColor Cyan
    Write-Host '  (the https://*.trycloudflare.com URL appears below)' -ForegroundColor DarkGray
    Write-Host ''

    & cloudflared tunnel --url "http://127.0.0.1:$Port"
}
finally {
    Write-Host ''
    Write-Host '  Shutting down dashboard ...' -ForegroundColor DarkGray
    if ($server -and -not $server.HasExited) {
        Stop-Process -Id $server.Id -Force -ErrorAction SilentlyContinue
    }
}
