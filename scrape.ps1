#!/usr/bin/env pwsh
# scrape.ps1 - Wrapper for ultra_scraper.py
# Usage: .\scrape.ps1 run urls.txt
#        .\scrape.ps1 get https://...
#        .\scrape.ps1 bulk urls.txt

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ToolPath = Join-Path $ScriptDir "ultra_scraper.py"
$VenvPython = Join-Path $ScriptDir ".venv\Scripts\python.exe"
$Python = if (Test-Path $VenvPython) { $VenvPython } else { "python" }

if (-not (Test-Path $ToolPath)) {
    Write-Error "ultra_scraper.py not found in $ScriptDir"
    exit 1
}

& $Python $ToolPath @args
