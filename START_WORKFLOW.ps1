$ErrorActionPreference = "Stop"

$workspace = Split-Path -Parent $MyInvocation.MyCommand.Path
$baseDir = Split-Path -Parent $workspace
$python = Join-Path $baseDir ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    $python = "python"
}

Set-Location -LiteralPath $workspace
& $python (Join-Path $workspace "START_WORKFLOW.py")
