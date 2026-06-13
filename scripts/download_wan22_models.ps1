param(
    [switch]$SkipT2V,
    [switch]$SkipLoras
)

$ErrorActionPreference = "Stop"

$root = Resolve-Path (Join-Path $PSScriptRoot "..\..")
$models = Join-Path $root "models"

$files = @(
    @{
        Group = "core"
        Name = "wan2.2_ti2v_5B_fp16.safetensors"
        Url = "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_ti2v_5B_fp16.safetensors"
        Dest = Join-Path $models "diffusion_models\Wan2.2\wan2.2_ti2v_5B_fp16.safetensors"
        Bytes = 9999658848
    },
    @{
        Group = "core"
        Name = "wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"
        Url = "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"
        Dest = Join-Path $models "diffusion_models\Wan2.2\wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors"
        Bytes = 14294742832
    },
    @{
        Group = "core"
        Name = "wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"
        Url = "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"
        Dest = Join-Path $models "diffusion_models\Wan2.2\wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors"
        Bytes = 14294742832
    },
    @{
        Group = "t2v"
        Name = "wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors"
        Url = "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors"
        Dest = Join-Path $models "diffusion_models\Wan2.2\wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors"
        Bytes = 14293923632
    },
    @{
        Group = "t2v"
        Name = "wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors"
        Url = "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/diffusion_models/wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors"
        Dest = Join-Path $models "diffusion_models\Wan2.2\wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors"
        Bytes = 14293923632
    },
    @{
        Group = "core"
        Name = "umt5_xxl_fp8_e4m3fn_scaled.safetensors"
        Url = "https://huggingface.co/Comfy-Org/Wan_2.1_ComfyUI_repackaged/resolve/main/split_files/text_encoders/umt5_xxl_fp8_e4m3fn_scaled.safetensors"
        Dest = Join-Path $models "text_encoders\umt5_xxl_fp8_e4m3fn_scaled.safetensors"
        Bytes = 6735906897
    },
    @{
        Group = "core"
        Name = "wan_2.1_vae.safetensors"
        Url = "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan_2.1_vae.safetensors"
        Dest = Join-Path $models "vae\wan_2.1_vae.safetensors"
        Bytes = 253815318
    },
    @{
        Group = "core"
        Name = "wan2.2_vae.safetensors"
        Url = "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/vae/wan2.2_vae.safetensors"
        Dest = Join-Path $models "vae\wan2.2_vae.safetensors"
        Bytes = 1409400960
    },
    @{
        Group = "lora"
        Name = "wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors"
        Url = "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors"
        Dest = Join-Path $models "loras\Wan2.2\wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors"
        Bytes = 1226977424
    },
    @{
        Group = "lora"
        Name = "wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors"
        Url = "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors"
        Dest = Join-Path $models "loras\Wan2.2\wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors"
        Bytes = 1226977424
    },
    @{
        Group = "lora"
        Name = "wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors"
        Url = "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors"
        Dest = Join-Path $models "loras\Wan2.2\wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors"
        Bytes = 1226977424
    },
    @{
        Group = "lora"
        Name = "wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors"
        Url = "https://huggingface.co/Comfy-Org/Wan_2.2_ComfyUI_Repackaged/resolve/main/split_files/loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors"
        Dest = Join-Path $models "loras\Wan2.2\wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors"
        Bytes = 1226977424
    }
)

foreach ($file in $files) {
    if ($SkipT2V -and $file.Group -eq "t2v") { continue }
    if ($SkipLoras -and $file.Group -eq "lora") { continue }

    $dest = $file.Dest
    $dir = Split-Path -Parent $dest
    New-Item -ItemType Directory -Force -Path $dir | Out-Null

    if (Test-Path $dest) {
        $existing = (Get-Item $dest).Length
        if ($existing -eq [int64]$file.Bytes) {
            Write-Host "[skip] $($file.Name)"
            continue
        }
        if ($existing -gt [int64]$file.Bytes) {
            throw "Existing file is larger than expected: $dest"
        }
        Write-Host "[resume] $($file.Name) ($existing / $($file.Bytes) bytes)"
    } else {
        Write-Host "[download] $($file.Name)"
    }

    & curl.exe `
        --location `
        --fail `
        --retry 12 `
        --retry-delay 5 `
        --retry-all-errors `
        --connect-timeout 30 `
        --continue-at - `
        --output $dest `
        $file.Url

    if ($LASTEXITCODE -ne 0) {
        throw "curl failed for $($file.Name) with exit code $LASTEXITCODE"
    }

    $actual = (Get-Item $dest).Length
    if ($actual -ne [int64]$file.Bytes) {
        throw "Size check failed for $($file.Name): got $actual, expected $($file.Bytes)"
    }
    Write-Host "[ok] $($file.Name)"
}

Write-Host "Wan2.2 model download/check complete."
