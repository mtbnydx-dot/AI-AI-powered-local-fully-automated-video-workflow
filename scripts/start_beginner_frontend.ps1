$ErrorActionPreference = "Stop"

$workspace = Split-Path -Parent $PSScriptRoot
$baseDir = if ($env:COMFY_BASE_DIR) { $env:COMFY_BASE_DIR } else { Split-Path -Parent $workspace }
$python = Join-Path $baseDir ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Python virtual environment not found: $python"
}

try {
    $status = Invoke-RestMethod -Uri "http://127.0.0.1:8000/system_stats" -TimeoutSec 3
    Write-Host "ComfyUI OK:" $status.system.comfyui_version
} catch {
    Write-Host "ComfyUI is not responding on http://127.0.0.1:8000"
    Write-Host "Please start ComfyUI Desktop first, then run this script again."
    throw
}

Set-Location -LiteralPath $workspace
Write-Host "Beginner frontend: http://127.0.0.1:7860"
& $python -m uvicorn beginner_frontend.app:app --host 127.0.0.1 --port 7860
