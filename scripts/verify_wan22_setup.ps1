param(
    [string]$ComfyUrl = "http://127.0.0.1:8000"
)

$ErrorActionPreference = "Stop"
$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$errors = New-Object System.Collections.Generic.List[string]

$expectedFiles = @(
    @{ Path = "models\diffusion_models\Wan2.2\wan2.2_ti2v_5B_fp16.safetensors"; Bytes = 9999658848 },
    @{ Path = "models\diffusion_models\Wan2.2\wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"; Bytes = 14294742832 },
    @{ Path = "models\diffusion_models\Wan2.2\wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"; Bytes = 14294742832 },
    @{ Path = "models\diffusion_models\Wan2.2\wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors"; Bytes = 14293923632 },
    @{ Path = "models\diffusion_models\Wan2.2\wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors"; Bytes = 14293923632 },
    @{ Path = "models\text_encoders\umt5_xxl_fp8_e4m3fn_scaled.safetensors"; Bytes = 6735906897 },
    @{ Path = "models\vae\wan_2.1_vae.safetensors"; Bytes = 253815318 },
    @{ Path = "models\vae\wan2.2_vae.safetensors"; Bytes = 1409400960 },
    @{ Path = "models\loras\Wan2.2\wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors"; Bytes = 1226977424 },
    @{ Path = "models\loras\Wan2.2\wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors"; Bytes = 1226977424 },
    @{ Path = "models\loras\Wan2.2\wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors"; Bytes = 1226977424 },
    @{ Path = "models\loras\Wan2.2\wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors"; Bytes = 1226977424 },
    @{ Path = "custom_nodes\ComfyUI-Frame-Interpolation\ckpts\rife\rife49.pth"; Bytes = 21345274 }
)

foreach ($file in $expectedFiles) {
    $fullPath = Join-Path $root $file.Path
    if (-not (Test-Path $fullPath)) {
        $errors.Add("Missing file: $fullPath")
        continue
    }
    $actual = (Get-Item $fullPath).Length
    if ($actual -ne [int64]$file.Bytes) {
        $errors.Add("Size mismatch: $fullPath ($actual != $($file.Bytes))")
    }
}

try {
    $stats = (Invoke-WebRequest -UseBasicParsing "$ComfyUrl/system_stats" -TimeoutSec 10).Content | ConvertFrom-Json
    Write-Host "ComfyUI: $($stats.system.comfyui_version)"
    Write-Host "PyTorch: $($stats.system.pytorch_version)"
    Write-Host "Device: $($stats.devices[0].name)"
} catch {
    $errors.Add("Could not reach ComfyUI at $ComfyUrl`: $($_.Exception.Message)")
}

try {
    $objectInfo = (Invoke-WebRequest -UseBasicParsing "$ComfyUrl/object_info" -TimeoutSec 30).Content | ConvertFrom-Json
    $nodeNames = $objectInfo.PSObject.Properties.Name
    foreach ($node in @("Wan22ImageToVideoLatent", "WanImageToVideo", "VHS_VideoCombine", "VHS_LoadVideo", "RIFE VFI", "SaveVideo")) {
        if ($nodeNames -notcontains $node) {
            $errors.Add("Missing node: $node")
        }
    }

    $unetInfo = (Invoke-WebRequest -UseBasicParsing "$ComfyUrl/object_info/UNETLoader" -TimeoutSec 10).Content
    foreach ($name in @(
        "wan2.2_ti2v_5B_fp16.safetensors",
        "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
        "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
        "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
        "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors"
    )) {
        if ($unetInfo -notlike "*$name*") {
            $errors.Add("UNETLoader does not list: $name")
        }
    }

    $clipInfo = (Invoke-WebRequest -UseBasicParsing "$ComfyUrl/object_info/CLIPLoader" -TimeoutSec 10).Content
    if ($clipInfo -notlike "*umt5_xxl_fp8_e4m3fn_scaled.safetensors*") {
        $errors.Add("CLIPLoader does not list UMT5.")
    }

    $vaeInfo = (Invoke-WebRequest -UseBasicParsing "$ComfyUrl/object_info/VAELoader" -TimeoutSec 10).Content
    foreach ($name in @("wan_2.1_vae.safetensors", "wan2.2_vae.safetensors")) {
        if ($vaeInfo -notlike "*$name*") {
            $errors.Add("VAELoader does not list: $name")
        }
    }
} catch {
    $errors.Add("Could not query object_info: $($_.Exception.Message)")
}

if ($errors.Count -gt 0) {
    Write-Host "`nFAILED:" -ForegroundColor Red
    foreach ($errorText in $errors) {
        Write-Host "- $errorText" -ForegroundColor Red
    }
    exit 1
}

Write-Host "`nWan2.2 local video workflow setup verified." -ForegroundColor Green
