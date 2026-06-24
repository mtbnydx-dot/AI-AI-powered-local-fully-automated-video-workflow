from __future__ import annotations

import argparse
import ast
import asyncio
import contextlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import importlib
import urllib.error
from pathlib import Path
from unittest.mock import patch


if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")


sys.dont_write_bytecode = True

ROOT = Path(__file__).resolve().parents[1]
RELEASE_PACKAGE = ROOT / "github_upload" / "wan22-local-video-workflow"
PACKAGE = RELEASE_PACKAGE if RELEASE_PACKAGE.exists() else ROOT


BASE_PYTHON_FILES = [
    ROOT / "START_WORKFLOW.py",
    ROOT / "beginner_frontend" / "app.py",
    ROOT / "scripts" / "prerequisite_doctor.py",
    ROOT / "scripts" / "install_workflow_assets.py",
    ROOT / "scripts" / "install_comfyui.py",
    ROOT / "scripts" / "release_smoke.py",
    ROOT / "scripts" / "clean_bootstrap_smoke.py",
]

PACKAGE_PYTHON_FILES = [
    PACKAGE / "START_WORKFLOW.py",
    PACKAGE / "beginner_frontend" / "app.py",
    PACKAGE / "scripts" / "prerequisite_doctor.py",
    PACKAGE / "scripts" / "install_workflow_assets.py",
    PACKAGE / "scripts" / "install_comfyui.py",
    PACKAGE / "scripts" / "release_smoke.py",
    PACKAGE / "scripts" / "clean_bootstrap_smoke.py",
]

PYTHON_FILES = BASE_PYTHON_FILES + ([] if PACKAGE == ROOT else PACKAGE_PYTHON_FILES)

BASE_JS_FILES = [
    ROOT / "beginner_frontend" / "static" / "app.js",
]

PACKAGE_JS_FILES = [
    PACKAGE / "beginner_frontend" / "static" / "app.js",
]

JS_FILES = BASE_JS_FILES + ([] if PACKAGE == ROOT else PACKAGE_JS_FILES)

POLLUTION_RE = re.compile(
    r"(__pycache__|\.pyc$|\.DS_Store$|\.log$|\.out\.log$|\.err\.log$|"
    r"\.safetensors$|\.ckpt$|\.pt$|\.pth$|\.gguf$|\.bin$|\.onnx$|"
    r"\.zip$|\.tar$|\.tar\.gz$|\.tgz$|\.7z$|\.rar$|"
    r"\.mp4$|\.mov$|\.mkv$|\.webm$|\.avi$|\.part$)",
    re.IGNORECASE,
)

TEXT_SCAN_SUFFIXES = {
    ".bat",
    ".command",
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".ps1",
    ".py",
    ".txt",
    ".yaml",
    ".yml",
}

LOCAL_PATH_PATTERNS = [
    ("local E drive workspace", re.compile(re.escape("E:\\" + "ai photo creat"), re.IGNORECASE)),
    ("local Windows user profile", re.compile(re.escape("C:\\Users\\" + "zlk"), re.IGNORECASE)),
    ("local macOS user profile", re.compile(re.escape("/Users/" + "bb" + "zlk"), re.IGNORECASE)),
    ("local macOS user name", re.compile(r"\b" + re.escape("bb" + "zlk") + r"\b", re.IGNORECASE)),
]


def rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT)).replace("\\", "/")
    except ValueError:
        return str(path)


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def ast_check() -> list[str]:
    checked: list[str] = []
    for path in PYTHON_FILES:
        check(path.exists(), f"missing Python file: {rel(path)}")
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        checked.append(rel(path))
    return checked


def js_check() -> list[str]:
    checked: list[str] = []
    node = shutil_which("node")
    if not node:
        return ["node not found; skipped JS syntax check"]
    for path in JS_FILES:
        check(path.exists(), f"missing JS file: {rel(path)}")
        result = subprocess.run([node, "--check", str(path)], capture_output=True, text=True)
        check(result.returncode == 0, f"JS syntax failed for {rel(path)}: {result.stderr}")
        checked.append(rel(path))
    return checked


def shutil_which(name: str) -> str | None:
    from shutil import which

    return which(name)


def manifest_check() -> dict[str, int]:
    sys.path.insert(0, str(ROOT))
    from beginner_frontend import app as frontend
    from scripts import install_workflow_assets as installer

    assets = frontend.workflow_asset_manifest(frontend.BASE_DIR)
    nodes = frontend.custom_node_manifest(frontend.BASE_DIR)
    asset_ids = {item["id"] for item in assets}
    installer_ids = {item["id"] for item in installer.MODEL_FILES}
    check(installer_ids <= asset_ids, "frontend manifest does not cover installer MODEL_FILES")
    check({"sample_keyframe", "ti2v_5b", "rife49", "realesrgan_x2"} <= asset_ids, "required asset ids missing")
    check({item["id"] for item in nodes} == {"video_helper_suite", "frame_interpolation"}, "custom node manifest mismatch")
    plan = frontend.install_plan_payload(
        profile="cuda-full",
        assets=frontend.collect_asset_checks(frontend.BASE_DIR),
        custom_nodes=frontend.collect_custom_node_checks(frontend.BASE_DIR),
        missing=[],
    )
    check(plan["asset_count"] >= 16, "cuda-full plan is unexpectedly small")
    check(plan["custom_node_count"] == 2, "cuda-full should include two custom nodes")
    check("disk" in plan and "ok" in plan["disk"], "install plan missing disk preflight")
    wan5b_items = {item["id"] for item in installer.selected_model_items("cuda-wan5b")}
    check({"ti2v_5b", "umt5", "wan22_vae", "rife49", "realesrgan_x2"} <= wan5b_items, "cuda-wan5b profile missing required assets")
    check("i2v_high" not in wan5b_items and "t2v_high" not in wan5b_items, "cuda-wan5b profile should not download A14B full assets")
    check("cuda-wan5b" in frontend.INSTALL_PROFILE_ASSETS, "frontend missing cuda-wan5b install profile")
    check(
        frontend.install_profile_for_hardware({"platform_strategy": "cuda_wan_workflow", "max_vram_gb": 96}) == "cuda-full",
        "80GB+ CUDA should default to cuda-full",
    )
    check(
        frontend.install_profile_for_hardware({"platform_strategy": "cuda_wan_workflow", "max_vram_gb": 48}) == "cuda-wan5b",
        "sub-80GB CUDA should default to cuda-wan5b",
    )
    check(
        frontend.install_profile_for_hardware({"platform_strategy": "post_only", "max_vram_gb": 0}) == "post-only",
        "non-accelerated systems should default to post-only",
    )
    disk_plan = installer.disk_space_plan(ROOT, 0)
    check(disk_plan["ok"] is True, "zero-byte disk plan should be ok")
    return {"installer_assets": len(installer_ids), "frontend_assets": len(asset_ids), "custom_nodes": len(nodes)}


def workflow_check() -> dict[str, int]:
    sys.path.insert(0, str(ROOT))
    from beginner_frontend import app as frontend

    graphs = [
        frontend.build_ti2v_prompt(
            prompt="test",
            negative="bad",
            image_name=frontend.DEFAULT_IMAGE,
            width=1280,
            height=704,
            length=81,
            fps=24,
            seed=1,
            steps=20,
            cfg=5.0,
        ),
        frontend.build_ltx_i2v_prompt(
            prompt="test",
            negative="bad",
            image_name=frontend.DEFAULT_IMAGE,
            width=512,
            height=320,
            length=25,
            fps=24,
            seed=1,
            steps=12,
            cfg=3.0,
        ),
        frontend.build_rife_prompt(video_name="sample.mp4", fps=24, multiplier=2),
        frontend.build_video_upscale_prompt(video_name="sample.mp4", fps=24),
    ]
    for graph in graphs:
        check(not frontend.validate_graph(graph), "workflow graph validation failed")
        check("%date:" not in json.dumps(graph), "workflow graph still contains date template")
    with tempfile.TemporaryDirectory(prefix="wan22_keyframe_check_") as temp_name:
        temp_base = Path(temp_name)
        temp_paths = {
            "base_dir": temp_base,
            "input_dir": temp_base / "input",
            "output_dir": temp_base / "output",
            "temp_dir": temp_base / "temp",
            "user_dir": temp_base / "user",
        }
        check(
            frontend.default_keyframe_errors(mode="ti2v_5b", has_upload=False, paths=temp_paths),
            "missing default keyframe should block image-video workflow preflight",
        )
        check(
            not frontend.default_keyframe_errors(mode="ti2v_5b", has_upload=True, paths=temp_paths),
            "uploaded keyframe should bypass default keyframe check",
        )
    return {"graphs": len(graphs)}


def hardware_matrix_check() -> dict[str, object]:
    sys.path.insert(0, str(ROOT))
    from beginner_frontend import app as frontend

    with tempfile.TemporaryDirectory(prefix="wan22_hardware_matrix_") as temp_name:
        base_dir = Path(temp_name)
        assets = [dict(item, ok=True) for item in frontend.workflow_asset_manifest(base_dir)]
        custom_nodes = [dict(item, ok=True) for item in frontend.custom_node_manifest(base_dir)]
        node_checks = [dict(item, ok=True) for item in frontend.collect_node_checks({})]
        tool_checks = [
            {"name": "ffmpeg", "ok": True},
            {"name": "ffmpeg:deflicker", "ok": True},
            {"name": "ffmpeg:hqdn3d", "ok": True},
        ]

        cases = [
            {
                "name": "cuda_96gb",
                "os": {"name": "Windows"},
                "hardware": {"accelerator": "cuda", "platform_strategy": "cuda_wan_workflow", "max_vram_gb": 96, "mac": {}},
                "install_profile": "cuda-full",
                "draft": "wan22_ti2v_5b_720p",
                "final": "wan22_i2v_a14b_720p",
            },
            {
                "name": "cuda_48gb",
                "os": {"name": "Windows"},
                "hardware": {"accelerator": "cuda", "platform_strategy": "cuda_wan_workflow", "max_vram_gb": 48, "mac": {}},
                "install_profile": "cuda-wan5b",
                "draft": "wan22_ti2v_5b_720p",
                "final": "wan22_ti2v_5b_final_720p",
            },
            {
                "name": "mac_16gb",
                "os": {"name": "Darwin"},
                "hardware": {
                    "accelerator": "mps",
                    "platform_strategy": "mac_mps",
                    "max_vram_gb": 0,
                    "front_torch_mps_ready": True,
                    "comfy_torch_mps_ready": False,
                    "mac_video_tier": "mac_ltx_low",
                    "mac": {"is_macos": True, "apple_silicon": True, "unified_memory_gb": 16},
                },
                "install_profile": "mac-low",
                "draft": "mac_ltx_low_i2v",
                "final": "mac_ltx_low_i2v",
            },
            {
                "name": "mac_128gb",
                "os": {"name": "Darwin"},
                "hardware": {
                    "accelerator": "mps",
                    "platform_strategy": "mac_mps",
                    "max_vram_gb": 0,
                    "front_torch_mps_ready": True,
                    "comfy_torch_mps_ready": False,
                    "mac_video_tier": "mac_wan5b_720p_experimental",
                    "mac": {"is_macos": True, "apple_silicon": True, "unified_memory_gb": 128},
                },
                "install_profile": "mac-wan5b",
                "draft": "mac_ltx_quality_i2v",
                "final": "mac_wan5b_720p_experimental",
            },
            {
                "name": "cpu_only",
                "os": {"name": "Windows"},
                "hardware": {"accelerator": "cpu", "platform_strategy": "post_only", "max_vram_gb": 0, "mac": {}},
                "install_profile": "post-only",
                "draft": "no_local_video_draft",
                "final": "no_local_video_final",
            },
        ]

        results: dict[str, dict[str, str]] = {}
        for case in cases:
            options = frontend.workflow_model_options(
                os_info=case["os"],
                hardware=case["hardware"],
                assets=assets,
                custom_nodes=custom_nodes,
                tool_checks=tool_checks,
                node_checks=node_checks,
                base_dir=base_dir,
            )
            recommended = options["recommended"]
            profile = frontend.install_profile_for_hardware(case["hardware"])
            check(profile == case["install_profile"], f"{case['name']} install profile mismatch: {profile}")
            check(recommended["draft"] == case["draft"], f"{case['name']} draft mismatch: {recommended['draft']}")
            check(recommended["final"] == case["final"], f"{case['name']} final mismatch: {recommended['final']}")
            results[case["name"]] = {
                "install_profile": profile,
                "draft": recommended["draft"],
                "final": recommended["final"],
            }

        wan5b_ids = {"ti2v_5b", "umt5", "wan22_vae", "rife49", "realesrgan_x2", "ultrasharp_x4", "sample_keyframe"}
        wan5b_only_assets = [dict(item, ok=item["id"] in wan5b_ids) for item in frontend.workflow_asset_manifest(base_dir)]
        wan5b_only_recommendations = frontend.model_recommendations(
            os_info={"name": "Windows"},
            hardware={"accelerator": "cuda", "platform_strategy": "cuda_wan_workflow", "max_vram_gb": 48, "mac": {}},
            assets=wan5b_only_assets,
            custom_nodes=custom_nodes,
            tool_checks=tool_checks,
            node_checks=node_checks,
            base_dir=base_dir,
        )
        formal = next(item for item in wan5b_only_recommendations if item["step"] == "正式片段")
        check(formal["status"] != "blocked", "48GB Wan5B-only profile should not be blocked by missing A14B assets")

        unsupported_preview = asyncio.run(
            frontend.workflow_preview(mode="unsupported", model_profile="no_local_video_draft")
        )
        check(not unsupported_preview["ok"], "unsupported video profile should fail workflow preflight")
        check(
            "后期处理路线" in " ".join(unsupported_preview.get("errors", [])),
            "unsupported video profile should explain the post-only route",
        )

    return {"cases": results}


def fallback_check() -> dict[str, str | bool]:
    sys.path.insert(0, str(ROOT))
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("COMFY_URL", None)
        sys.modules.pop("START_WORKFLOW", None)
        launcher = importlib.import_module("START_WORKFLOW")
    from beginner_frontend import app as frontend
    from scripts import install_comfyui, install_workflow_assets
    from scripts import prerequisite_doctor

    check(launcher.APP_VERSION == frontend.APP_VERSION, "launcher and frontend app versions differ")
    check("http://127.0.0.1:8188" in launcher.common_comfy_urls(), "launcher ComfyUI autodiscovery must include 8188")
    check("COMFY_URL" not in launcher.build_frontend_env(comfy_detected=False), "launcher should not pin COMFY_URL when no ComfyUI was discovered")
    check(launcher.build_frontend_env(comfy_detected=True).get("COMFY_URL") == launcher.COMFY_URL, "launcher should pass discovered ComfyUI URL to frontend")
    with patch.object(launcher, "COMFY_URL_CONFIGURED", True), patch.object(launcher, "COMFY_URL", "http://127.0.0.1:8188"):
        check(
            launcher.build_frontend_env(comfy_detected=False).get("COMFY_URL") == "http://127.0.0.1:8188",
            "launcher should respect explicit COMFY_URL even before discovery",
        )
    with patch("beginner_frontend.app.COMFY_URL_CONFIGURED", False):
        check("http://127.0.0.1:8188" in frontend.common_comfy_urls(), "frontend ComfyUI autodiscovery must include 8188")
    check(frontend.COMFY_DISCOVERY_TIMEOUT <= 2.0, "frontend ComfyUI discovery timeout should stay short")
    check(frontend.COMFY_DISCOVERY_FAILURE_TTL <= 5.0, "failed ComfyUI discovery cache should refresh quickly")
    launcher_text = (ROOT / "START_WORKFLOW.py").read_text(encoding="utf-8")
    check(r".\START_WORKFLOW.bat" in launcher_text, "Windows Python help should point beginners to START_WORKFLOW.bat")
    check('argv[0] == "--check"' in launcher_text, "Python launcher should support --check without starting the server")
    check("def print_usage()" in launcher_text and '"--help"' in launcher_text, "Python launcher should support --help without starting the server")
    check("--no-browser" in launcher_text and "Ready:" in launcher_text, "Python launcher should support headless frontend startup for clean bootstrap smoke")
    check("--accept-package-agreements" in launcher_text, "Python launcher help should include winget agreement flags")
    check("https://www.python.org/downloads/windows/" in launcher_text, "Python launcher should include official Windows Python download fallback")
    check("frontend dependency setup failed" in launcher_text, "Python launcher should explain frontend dependency failures")
    check(
        "bootstrap_subprocess_env" in launcher_text and "PIP_INDEX_URL" in launcher_text and "proxy_url" in launcher_text,
        "launcher should reuse saved proxy and pip mirror settings when installing frontend dependencies",
    )
    check(
        "--set-pip-index" in launcher_text and "--set-proxy" in launcher_text and "--show-download-settings" in launcher_text,
        "launcher should expose pre-frontend download setting commands for first-run pip failures",
    )
    check(
        "ensure_pip_available" in launcher_text and "ensurepip" in launcher_text and "run_pip" in launcher_text,
        "launcher should repair missing pip and retry frontend dependency installs",
    )
    check(
        "offer_frontend_dependency_retry" in launcher_text and "保存网络设置并立即重试" in launcher_text,
        "launcher should offer an interactive pip mirror/proxy rescue before the frontend can open",
    )
    launcher_pip_calls: list[list[str]] = []

    def flaky_launcher_pip(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        launcher_pip_calls.append(command)
        if len(launcher_pip_calls) == 1:
            raise subprocess.CalledProcessError(1, command)
        return subprocess.CompletedProcess(command, 0, "", "")

    with patch.object(launcher.subprocess, "run", side_effect=flaky_launcher_pip), patch.object(launcher.time, "sleep", return_value=None):
        launcher.run_pip([str(Path("python")), "-m", "pip", "install", "fastapi"], env={})
    check(len(launcher_pip_calls) == 2, "launcher pip installs should retry after a transient failure")
    with tempfile.TemporaryDirectory(prefix="launcher_download_settings_") as temp_name:
        config_path = Path(temp_name) / ".wan22_workflow_config.json"
        with patch.object(launcher, "LOCAL_CONFIG_FILE", config_path):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                result = launcher.handle_download_settings_cli(
                    [
                        "--set-pip-index",
                        "https://pypi.example/simple",
                        "--set-proxy",
                        "http://user:secret@127.0.0.1:7890",
                    ]
                )
            saved_config = launcher.read_local_config()
            check(result == 0, "launcher download setting command should succeed")
            check(saved_config.get("pip_index_url") == "https://pypi.example/simple", "launcher should save pip mirror before frontend dependencies install")
            check(saved_config.get("proxy_url") == "http://user:secret@127.0.0.1:7890", "launcher should save proxy before frontend dependencies install")
            check("secret" not in output.getvalue() and "***:***@127.0.0.1:7890" in output.getvalue(), "launcher should redact proxy credentials when showing settings")
            with contextlib.redirect_stdout(io.StringIO()):
                result = launcher.handle_download_settings_cli(["--clear-download-settings"])
            check(result == 0 and not config_path.exists(), "launcher should clear saved download settings")
            output = io.StringIO()
            with patch.object(launcher, "stdin_is_interactive", return_value=True), patch(
                "builtins.input",
                side_effect=["Y", "https://pypi.retry/simple", "http://user:secret@127.0.0.1:7890"],
            ), contextlib.redirect_stdout(output):
                should_retry = launcher.offer_frontend_dependency_retry(RuntimeError("pip failed"))
            saved_config = launcher.read_local_config()
            check(should_retry is True, "launcher interactive dependency rescue should retry after saving settings")
            check(saved_config.get("pip_index_url") == "https://pypi.retry/simple", "launcher interactive rescue should save pip mirror")
            check(saved_config.get("proxy_url") == "http://user:secret@127.0.0.1:7890", "launcher interactive rescue should save proxy")
            check("secret" not in output.getvalue() and "***:***@127.0.0.1:7890" in output.getvalue(), "launcher interactive rescue should redact proxy credentials")
    ps1_text = (ROOT / "START_WORKFLOW.ps1").read_text(encoding="utf-8")
    command_text = (ROOT / "START_WORKFLOW.command").read_text(encoding="utf-8")
    bat_text = (ROOT / "START_WORKFLOW.bat").read_text(encoding="utf-8")
    release_smoke_text = (ROOT / "scripts" / "release_smoke.py").read_text(encoding="utf-8")
    clean_bootstrap_text = (ROOT / "scripts" / "clean_bootstrap_smoke.py").read_text(encoding="utf-8")
    prerequisite_text = (ROOT / "scripts" / "prerequisite_doctor.py").read_text(encoding="utf-8")
    requirements_text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    requirement_lines = {line.split(">=", 1)[0]: line for line in requirements_text.splitlines() if ">=" in line}
    for dependency in ("fastapi", "httpx", "imageio-ffmpeg", "python-multipart", "uvicorn[standard]"):
        check("<1.0" in requirement_lines.get(dependency, ""), f"{dependency} should have a conservative upper bound for first-run stability")
    check("reconfigure" in prerequisite_text and 'encoding="utf-8"' in prerequisite_text, "prerequisite doctor should force UTF-8 CLI output on Windows")
    check("reconfigure" in release_smoke_text and 'encoding="utf-8"' in release_smoke_text, "release smoke should force UTF-8 CLI output and subprocess decoding")
    check("START_WORKFLOW.ps1 OK" in ps1_text and "@scriptArgs" in ps1_text, "PowerShell launcher should support --check and forward script args")
    check("$pythonCommand = @(Find-Python)" in ps1_text, "PowerShell launcher should preserve a single Python path with spaces as one array item")
    check("--help" in ps1_text and "Beginner path" in ps1_text, "PowerShell launcher should support --help without bootstrapping Python")
    check("--no-browser" in ps1_text, "PowerShell launcher help should expose headless startup")
    check("--set-hf-endpoint" in ps1_text and "--set-pip-index" in ps1_text and "--show-download-settings" in ps1_text, "PowerShell help should expose pre-frontend download settings")
    check("$launcher @scriptArgs" in ps1_text, "PowerShell launcher should pass START_WORKFLOW.py before script args")
    check("--accept-source-agreements" in ps1_text and "Install-PythonWithWinget" in ps1_text, "PowerShell launcher should install Python with winget agreement flags and fallback")
    check("Open-PythonDownloadPage" in ps1_text and "https://www.python.org/downloads/windows/" in ps1_text, "PowerShell launcher should offer a no-winget Python download fallback")
    check('"$@"' in command_text and "START_WORKFLOW.command OK" in command_text, "macOS launcher should support --check and forward args")
    check("--help" in command_text and "Beginner path" in command_text, "macOS launcher should support --help without bootstrapping Python")
    check("--no-browser" in command_text, "macOS launcher help should expose headless startup")
    check("--set-hf-endpoint" in command_text and "--set-pip-index" in command_text and "--show-download-settings" in command_text, "macOS help should expose pre-frontend download settings")
    check("/opt/homebrew/bin/python3.12" in command_text and "bash ./START_WORKFLOW.command" in command_text, "macOS launcher should check common Homebrew/Python paths and explain bash fallback")
    check("HOMEBREW_INSTALL_URL" in command_text and "load_homebrew_path" in command_text, "macOS launcher should offer a Homebrew/Python bootstrap path")
    check("open \"$PYTHON_DOWNLOAD_URL\"" in command_text, "macOS launcher should offer the official Python download page when package managers are unavailable")
    check('START_WORKFLOW.ps1" %*' in bat_text, "Windows batch launcher should forward args to PowerShell")
    check("--help" in bat_text and "Beginner path" in bat_text, "Windows batch launcher should support --help without starting PowerShell")
    check("--no-browser" in bat_text, "Windows batch launcher help should expose headless startup")
    check("--set-hf-endpoint" in bat_text and "--set-pip-index" in bat_text and "--show-download-settings" in bat_text, "Windows batch help should expose pre-frontend download settings")
    check("--accept-package-agreements" in bat_text, "Windows batch fallback should include winget agreement flags")
    check("https://www.python.org/downloads/windows/" in bat_text, "Windows batch launcher should print the official Python download page")
    check("START_WORKFLOW.bat" in release_smoke_text and "start_beginner_frontend.ps1" in release_smoke_text, "release smoke should cover packaged Windows launchers")
    check("START_WORKFLOW.py\", \"--no-browser\"" in clean_bootstrap_text and "created_venv" in clean_bootstrap_text, "clean bootstrap smoke should verify no-venv first-run frontend startup")
    check("taskkill" in clean_bootstrap_text and '"/T"' in clean_bootstrap_text, "clean bootstrap smoke should terminate the Windows frontend process tree")
    check("prerequisites_have_python_download_fallback" in release_smoke_text, "release smoke should verify package-manager-free Python fallback commands")
    check("run_install_profile_matrix" in release_smoke_text and "mac_wan5b_excludes_a14b" in release_smoke_text, "release smoke should verify all beginner install profiles")
    check("full_setup_dry_run_ok" in release_smoke_text and "/api/bootstrap/full-setup" in release_smoke_text, "release smoke should cover one-click full setup dry-run")
    check("video_smoke_disconnected_guidance" in release_smoke_text and "post_json_expect_error" in release_smoke_text, "release smoke should verify beginner guidance when ComfyUI is disconnected")
    frontend_js_text = (ROOT / "beginner_frontend" / "static" / "app.js").read_text(encoding="utf-8")
    check("ComfyUI 已运行" in frontend_js_text, "bootstrap UI should disable start button when ComfyUI is already running")
    check("先安装 ComfyUI" in frontend_js_text, "bootstrap UI should guide beginners to install before starting")
    check("/api/diagnostics" in frontend_js_text, "frontend should expose copyable diagnostics")
    check("prerequisiteChecks" in frontend_js_text, "frontend should render first-run prerequisite checks")
    check("startSelfTest" in frontend_js_text and "/api/self-test" in frontend_js_text, "frontend should expose one-click self-test")
    check("startFullSetup" in frontend_js_text and "/api/bootstrap/full-setup" in frontend_js_text, "frontend should expose one-click full environment setup")
    check(
        "updateFullSetupAvailability" in frontend_js_text
        and "正在刷新当前档位安装计划" in frontend_js_text
        and "安装计划刷新失败，请重新侦测后再一键准备" in frontend_js_text,
        "full setup button should follow selected install profile disk preflight",
    )
    check(
        "async function loadInstallPlan" in frontend_js_text
        and "setFullSetupConfirming(false);\n  setWorkflowAssetInstallConfirming(false);" in frontend_js_text,
        "profile changes and environment refresh should clear stale install confirmations",
    )
    check("PyTorch 下载源" in frontend_js_text, "full setup confirmation should mention download-source network preflight")
    check("跳过源码版 ComfyUI 安装/更新" in frontend_js_text and "active base" in frontend_js_text, "full setup confirmation should explain running ComfyUI/Desktop behavior")
    check(
        "一键准备环境启动失败" in frontend_js_text
        and "一键准备环境轮询失败" in frontend_js_text
        and frontend_js_text.count("await loadEnvironment();") >= 5,
        "full setup failure paths should reload environment state so buttons recover",
    )
    check("startVideoSmokeTest" in frontend_js_text and "/api/video-smoke-test" in frontend_js_text, "frontend should expose one-click real generation smoke test")
    check("detail.actions" in frontend_js_text and "下一步：" in frontend_js_text, "frontend should render structured API recovery actions")
    check("formatJobFailureHint" in frontend_js_text, "frontend should render structured install failure hints")
    check("installPlanRequestId" in frontend_js_text, "install plan refresh should ignore stale responses")
    check("浏览器阻止剪贴板写入" in frontend_js_text, "diagnostics copy should fall back when clipboard is blocked")
    frontend_py_text = (ROOT / "beginner_frontend" / "app.py").read_text(encoding="utf-8")
    check('@app.get("/api/diagnostics")' in frontend_py_text, "backend diagnostics endpoint missing")
    check('@app.get("/api/prerequisites")' in frontend_py_text, "backend prerequisites endpoint missing")
    check('@app.post("/api/self-test")' in frontend_py_text, "backend self-test endpoint missing")
    check('@app.post("/api/bootstrap/full-setup")' in frontend_py_text, "backend full setup endpoint missing")
    check("run_full_setup_worker" in frontend_py_text and "dry_run" in frontend_py_text, "backend should orchestrate full setup with dry-run support")
    check(
        "skip_comfy_install" in frontend_py_text
        and "source_install_skipped" in frontend_py_text
        and "use_local_comfy_python" in frontend_py_text
        and "running_main_py == local_main_py" in frontend_py_text
        and "bool(runtime.get(\"source_ready\"))" in frontend_py_text,
        "full setup should skip source install and reuse the right Python for running or detected ComfyUI runtimes",
    )
    check(
        "def run_json_command" in frontend_py_text
        and 'encoding="utf-8"' in frontend_py_text
        and 'errors="replace"' in frontend_py_text,
        "backend subprocess capture should decode UTF-8 CLI output for self-test",
    )
    check('@app.post("/api/video-smoke-test")' in frontend_py_text, "backend video smoke-test endpoint missing")
    check("choose_video_smoke_option" in frontend_py_text and "VIDEO_SMOKE_PRIORITY" in frontend_py_text, "backend should choose a conservative smoke-test model")
    check(
        "COMFY_START_JOB_ID" in frontend_py_text
        and "wait_for_comfyui_ready_worker" in frontend_py_text
        and "ComfyUI 已就绪" in frontend_py_text,
        "start ComfyUI job should complete when /system_stats becomes ready instead of waiting for process exit",
    )
    check("comfy_not_connected_detail" in frontend_py_text and "一键准备环境" in frontend_py_text, "ComfyUI connection failures should include beginner recovery actions")
    check(
        '"--base-dir"' in frontend_py_text and '"--install-dir"' in frontend_py_text and "prerequisite_doctor.py" in frontend_py_text,
        "self-test prerequisite doctor should use the same base/install dirs as the frontend",
    )
    check(
        "def comfy_runtime_info" in frontend_py_text
        and "desktop_runtime" in frontend_py_text
        and "bundled_comfy_main_py" in frontend_py_text
        and "runtime_source_ready" in frontend_py_text
        and "desktop_available" in frontend_py_text,
        "frontend should detect source ComfyUI, ComfyUI Desktop bundled runtime, and Desktop app fallback",
    )
    check(
        "looks_like_comfy_base" in prerequisite_text
        and "default_base_dir" in prerequisite_text
        and "workspace.parent" in prerequisite_text,
        "prerequisite doctor should use the same parent-ComfyUI base discovery as the launcher/frontend",
    )
    check(
        "desktop_comfy_main_candidates" in prerequisite_text
        and "looks_like_comfy_desktop_runtime" in prerequisite_text
        and "runtime\": \"desktop\"" in prerequisite_text,
        "prerequisite doctor should treat ComfyUI Desktop/runtime directories as usable instead of blocking them",
    )
    check('install_profile == "cuda-wan5b"' in frontend_py_text, "validate should cover cuda-wan5b workflows")
    index_text = (ROOT / "beginner_frontend" / "static" / "index.html").read_text(encoding="utf-8")
    styles_text = (ROOT / "beginner_frontend" / "static" / "styles.css").read_text(encoding="utf-8")
    check("installProfileSelector" in index_text, "environment UI should expose install profile selection")
    check(
        "hfEndpointInput" in index_text
        and "pipIndexInput" in index_text
        and "proxyUrlInput" in index_text
        and "saveDownloadSettingsButton" in index_text
        and "testDownloadSourcesButton" in index_text,
        "environment UI should expose Hugging Face, pip mirror, proxy settings, and a download-source test button",
    )
    check("runFullSetupButton" in index_text, "environment UI should expose one-click full setup button")
    check("runSelfTestButton" in index_text, "environment UI should expose one-click self-test button")
    check("runVideoSmokeButton" in index_text, "environment UI should expose real generation smoke-test button")
    check("prerequisiteChecks" in index_text, "environment UI should expose prerequisite checks")
    check("copyDiagnosticsButton" in index_text, "result panel should expose diagnostics copy button")
    check("downloadDiagnosticsButton" in index_text, "result panel should expose diagnostics download button")
    check(
        ".download-source-controls" in styles_text and "auto-fit" in styles_text and "minmax(240px, 1fr)" in styles_text,
        "download-source controls should use a responsive grid instead of cramped fixed columns",
    )
    check("/api/install-plan" in frontend_js_text, "frontend should refresh install plans when profile changes")
    check(
        "/api/settings" in frontend_js_text
        and "renderDownloadSettings" in frontend_js_text
        and "proxy_url" in frontend_js_text
        and "pip_index_url" in frontend_js_text,
        "frontend should save and render download-source/proxy/pip settings",
    )
    check(
        "testDownloadSources" in frontend_js_text
        and "/api/prerequisites?network=true" in frontend_js_text
        and "renderDownloadSourceTest" in frontend_js_text,
        "frontend should let beginners test saved download sources before starting installation",
    )
    check(
        "persistDownloadSettings({ refreshAfterSave: false })" in frontend_js_text
        and "正在保存当前填写的下载源并测试连通性" in frontend_js_text,
        "download-source test should save the current form values before network testing",
    )
    check(
        frontend_js_text.index('await loadBootstrap();', frontend_js_text.index("async function testDownloadSources"))
        < frontend_js_text.index('fetch("/api/prerequisites?network=true")')
        < frontend_js_text.index("renderDownloadSourceTest(data)"),
        "download-source test should refresh setup state before rendering the final reachability result",
    )
    check("downloadDiagnostics" in frontend_js_text and "wan22-diagnostics-" in frontend_js_text and "URL.createObjectURL" in frontend_js_text, "frontend should download diagnostics as a JSON package")
    check(
        "fullSetupDownloadHosts" in frontend_js_text and "workflowAssetDownloadHosts" in frontend_js_text and "friendlyDownloadHostLabel" in frontend_js_text,
        "frontend confirmation prompts should list only the download hosts required by the selected setup path",
    )
    prerequisite_report = prerequisite_doctor.build_prerequisite_report(ROOT, ROOT, ROOT / "ComfyUI", include_network=False)
    check("checks" in prerequisite_report and any(item["id"] == "python" for item in prerequisite_report["checks"]), "prerequisite doctor should report Python readiness")
    check("install_commands" in prerequisite_report, "prerequisite doctor should include install commands")
    frontend_commands = prerequisite_report["install_commands"].get("frontend", [])
    check(
        any("ensurepip" in command for command in frontend_commands)
        and any("pip install -U pip" in command for command in frontend_commands),
        "prerequisite doctor frontend repair commands should include ensurepip and pip upgrade",
    )
    frontend_check = next(item for item in prerequisite_report["checks"] if item["id"] == "frontend_modules")
    check("source" in frontend_check and "python" in frontend_check, "prerequisite doctor should identify which Python environment was checked")
    check(
        {"github.com", "huggingface.co", "pypi.org", "download.pytorch.org"}.issubset(set(prerequisite_doctor.DOWNLOAD_HOSTS)),
        "network preflight should cover GitHub, Hugging Face, PyPI, and PyTorch downloads",
    )
    official_url = "https://huggingface.co/Org/Repo/resolve/main/model.safetensors"
    mirrored_url = install_workflow_assets.effective_download_url(official_url, "https://hf-mirror.example/root")
    check(
        mirrored_url == "https://hf-mirror.example/root/Org/Repo/resolve/main/model.safetensors",
        "model downloader should rewrite Hugging Face URLs to the configured endpoint without changing the repository path",
    )
    with patch.dict(os.environ, {"WAN22_HF_ENDPOINT": "https://hf-mirror.example"}, clear=False):
        targets = prerequisite_doctor.download_targets()
    check(
        "hf-mirror.example" in targets and "huggingface.co" not in targets,
        "network preflight should test the configured Hugging Face mirror instead of the official host",
    )
    with patch("beginner_frontend.app.saved_hf_endpoint", return_value="https://hf-mirror.example"), patch(
        "beginner_frontend.app.env_hf_endpoint",
        return_value="",
    ):
        check(frontend.effective_preflight_host("huggingface.co") == "hf-mirror.example", "frontend preflight should map Hugging Face requirements to the configured mirror host")
    with patch.dict(os.environ, {"PIP_INDEX_URL": "https://pypi.example/simple"}, clear=False):
        pip_targets = prerequisite_doctor.download_targets()
    check(
        "pypi.example" in pip_targets and "pypi.org" not in pip_targets,
        "network preflight should test the configured pip mirror instead of the official PyPI host",
    )
    with patch("beginner_frontend.app.saved_pip_index_url", return_value="https://pypi.example/simple"), patch(
        "beginner_frontend.app.env_pip_index_url",
        return_value="",
    ):
        check(frontend.effective_preflight_host("pypi.org") == "pypi.example", "frontend preflight should map PyPI requirements to the configured pip mirror host")
    check(frontend.redact_url("http://user:secret@127.0.0.1:7890") == "http://***:***@127.0.0.1:7890", "diagnostics should redact proxy credentials")
    check(
        frontend.redact_sensitive_text("proxy http://user:secret@127.0.0.1:7890/path") == "proxy http://***:***@127.0.0.1:7890/path",
        "diagnostics log redaction should hide URL credentials",
    )
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("HTTPS_PROXY", None)
        os.environ.pop("HTTP_PROXY", None)
        os.environ.pop("https_proxy", None)
        os.environ.pop("http_proxy", None)
        os.environ.pop("PIP_INDEX_URL", None)
        with patch("beginner_frontend.app.saved_proxy_url", return_value="http://127.0.0.1:7890"), patch(
            "beginner_frontend.app.saved_pip_index_url",
            return_value="https://pypi.example/simple",
        ):
            proxy_env = frontend.download_subprocess_env()
    check(
        proxy_env.get("HTTPS_PROXY") == "http://127.0.0.1:7890" and proxy_env.get("http_proxy") == "http://127.0.0.1:7890",
        "download subprocess env should propagate saved proxy settings to installers",
    )
    check(
        proxy_env.get("PIP_INDEX_URL") == "https://pypi.example/simple",
        "download subprocess env should propagate saved pip mirror settings to installers",
    )
    with patch.dict(os.environ, {"HTTPS_PROXY": "http://user:secret@proxy.local:8080"}, clear=False), patch(
        "scripts.prerequisite_doctor.url_reachable",
        return_value=True,
    ):
        network_check = prerequisite_doctor.check_network()
    check(
        "secret" not in str(network_check) and "***:***@proxy.local:8080" in str(network_check),
        "prerequisite network report should redact proxy credentials",
    )
    check('@app.post("/api/settings")' in frontend_py_text and "download_settings_payload" in frontend_py_text, "backend should expose persistent download-source settings")
    check(
        'label="ComfyUI 安装", env=download_subprocess_env()' in frontend_py_text
        and "args=(job_id, command, download_subprocess_env())" in frontend_py_text,
        "saved proxy settings should be passed to both full-setup and standalone ComfyUI installers",
    )
    check(
        "required_download_hosts_for_comfy_install" in frontend_py_text
        and "ComfyUI 安装前网络预检失败" in frontend_py_text,
        "standalone ComfyUI install should run download-source preflight before starting installers",
    )
    http_403 = urllib.error.HTTPError("https://download.pytorch.org/", 403, "Forbidden", hdrs=None, fp=None)
    with patch("scripts.prerequisite_doctor.urllib.request.urlopen", side_effect=http_403):
        check(prerequisite_doctor.url_reachable("https://download.pytorch.org/") is True, "network preflight should treat HTTP 4xx as reachable")
    check("--accept-source-agreements" in prerequisite_text and "start https://www.python.org/downloads/windows/" in prerequisite_text, "prerequisite doctor Windows install commands should match beginner launchers")
    check("open https://www.python.org/downloads/macos/" in prerequisite_text, "prerequisite doctor macOS install commands should match beginner launchers")
    network_hint = frontend.classify_install_failure(["[error] Could not download model: timed out"], return_code=1)
    check(network_hint["category"] == "network" and any(".part" in action for action in network_hint["actions"]), "network failures should explain .part retry behavior")
    disk_hint = frontend.classify_install_failure(["Not enough free disk space"], return_code=1)
    check(disk_hint["category"] == "disk", "disk failures should be classified")
    missing_script_hint = frontend.classify_install_failure(["安装脚本不存在：scripts/install_workflow_assets.py"], return_code=1)
    check(missing_script_hint["category"] == "missing_script", "missing installer scripts should be classified")
    fake_job_id = "self_check_failure_hint"
    frontend.INSTALL_JOBS[fake_job_id] = {
        "id": fake_job_id,
        "kind": "install_assets",
        "status": "failed",
        "completed": True,
        "created_at": 1,
        "log": ["Could not download from huggingface.co: timed out"],
    }
    frontend.attach_failure_hint(frontend.INSTALL_JOBS[fake_job_id])
    recent_jobs = frontend.recent_jobs_payload()
    check(any(item.get("id") == fake_job_id and item.get("failure_hint") for item in recent_jobs), "diagnostics should include recent failed job hints")
    frontend.INSTALL_JOBS.pop(fake_job_id, None)
    redaction_job_id = "self_check_redacted_log"
    frontend.INSTALL_JOBS[redaction_job_id] = {
        "id": redaction_job_id,
        "kind": "install_assets",
        "status": "failed",
        "completed": True,
        "created_at": 2,
        "log": ["proxy=http://user:secret@127.0.0.1:7890"],
    }
    redacted_jobs = frontend.recent_jobs_payload()
    frontend.INSTALL_JOBS.pop(redaction_job_id, None)
    check("secret" not in str(redacted_jobs) and "***:***@127.0.0.1:7890" in str(redacted_jobs), "recent job diagnostics should redact URL credentials in logs")
    if sys.platform.startswith("win"):
        windows_python_paths = launcher.common_windows_python_paths()
        check(any("Python312" in str(path) for path in windows_python_paths), "Windows Python 3.12 path fallback missing")
        check("Get-CommonPythonPaths" in ps1_text, "PowerShell launcher missing common Python path fallback")
    with patch("beginner_frontend.app.shutil.which", return_value=None):
        payload = frontend.bootstrap_status_payload(False)
        check(payload["can_install_comfyui"] is True, "ComfyUI install should be available without git")
        check(payload["git_ready"] is False, "git_ready mock failed")
    full_setup_job_id = "self_check_full_setup_skip"
    frontend.INSTALL_JOBS[full_setup_job_id] = {
        "id": full_setup_job_id,
        "kind": "full_setup",
        "status": "queued",
        "completed": False,
        "created_at": 1,
        "dry_run": True,
        "log": [],
    }
    captured_commands: list[tuple[str, list[str]]] = []
    captured_command_envs: list[tuple[str, dict[str, str] | None]] = []

    def fake_logged_command(job: dict, command: list[str], *, label: str, **_: object) -> int:
        captured_commands.append((label, command))
        job["log"].append(f"{label}: fake")
        return 0

    with patch("beginner_frontend.app.run_logged_command", side_effect=fake_logged_command):
        frontend.run_full_setup_worker(
            full_setup_job_id,
            backend="skip",
            profile="post-only",
            base_dir=ROOT,
            dry_run=True,
            skip_comfy_install=True,
            use_local_comfy_python=False,
        )
    full_setup_job = frontend.INSTALL_JOBS.pop(full_setup_job_id)
    check(full_setup_job["status"] == "success", "full setup skip-source dry-run should succeed")
    check(not any(label == "ComfyUI 安装" for label, _ in captured_commands), "running ComfyUI full setup should skip source ComfyUI install")
    asset_commands = [command for label, command in captured_commands if label == "模型/节点安装"]
    check(asset_commands and "--comfy-python" not in asset_commands[0], "external running ComfyUI setup should not pass local comfy-python")

    captured_commands.clear()

    def fake_logged_command_with_env(job: dict, command: list[str], *, label: str, env: dict[str, str] | None = None, **_: object) -> int:
        captured_commands.append((label, command))
        captured_command_envs.append((label, env))
        job["log"].append(f"{label}: fake")
        return 0

    proxy_full_setup_job_id = "self_check_full_setup_proxy"
    frontend.INSTALL_JOBS[proxy_full_setup_job_id] = {
        "id": proxy_full_setup_job_id,
        "kind": "full_setup",
        "status": "queued",
        "completed": False,
        "created_at": 1,
        "dry_run": True,
        "log": [],
    }
    with patch.dict(os.environ, {}, clear=True), patch(
        "beginner_frontend.app.saved_proxy_url",
        return_value="http://127.0.0.1:7890",
    ), patch(
        "beginner_frontend.app.saved_pip_index_url",
        return_value="https://pypi.example/simple",
    ), patch(
        "beginner_frontend.app.run_logged_command",
        side_effect=fake_logged_command_with_env,
    ):
        frontend.run_full_setup_worker(
            proxy_full_setup_job_id,
            backend="skip",
            profile="post-only",
            base_dir=ROOT,
            dry_run=True,
            skip_comfy_install=False,
            use_local_comfy_python=False,
        )
    proxy_full_setup_job = frontend.INSTALL_JOBS.pop(proxy_full_setup_job_id)
    check(proxy_full_setup_job["status"] == "success", "full setup proxy dry-run should succeed")
    env_by_label = {label: env or {} for label, env in captured_command_envs}
    check(
        env_by_label.get("ComfyUI 安装", {}).get("HTTPS_PROXY") == "http://127.0.0.1:7890",
        "full setup should pass saved proxy settings to the ComfyUI installer",
    )
    check(
        env_by_label.get("模型/节点安装", {}).get("HTTPS_PROXY") == "http://127.0.0.1:7890",
        "full setup should pass saved proxy settings to the workflow asset installer",
    )
    check(
        env_by_label.get("ComfyUI 安装", {}).get("PIP_INDEX_URL") == "https://pypi.example/simple"
        and env_by_label.get("模型/节点安装", {}).get("PIP_INDEX_URL") == "https://pypi.example/simple",
        "full setup should pass saved pip mirror settings to both installers",
    )
    network_fail_job_id = "self_check_full_setup_network_fail"
    frontend.INSTALL_JOBS[network_fail_job_id] = {
        "id": network_fail_job_id,
        "kind": "full_setup",
        "status": "queued",
        "completed": False,
        "created_at": 1,
        "dry_run": False,
        "log": [],
    }

    def fake_prerequisite_report(*args: object, **kwargs: object) -> dict:
        check(kwargs.get("include_network") is True, "full setup should request network preflight before real downloads")
        return {
            "checks": [
                {
                    "id": "network",
                    "label": "下载网络",
                    "status": "warn",
                    "blocking": False,
                    "message": "部分下载站点不可连接：Hugging Face",
                    "action": "检查代理、DNS、防火墙或稍后重试。",
                    "reachable": {"github.com": True, "huggingface.co": False, "pypi.org": True, "download.pytorch.org": True},
                }
            ]
        }

    def fail_if_command_runs(*args: object, **kwargs: object) -> int:
        raise AssertionError("installer command should not run when network preflight fails")

    network_fail_assets = [
        {
            "id": "rife49",
            "ok": False,
            "installable": True,
            "download_host": "huggingface.co",
            "local_available": False,
        }
    ]
    network_fail_nodes: list[dict[str, object]] = []
    with patch("beginner_frontend.app.BUILD_PREREQUISITE_REPORT", side_effect=fake_prerequisite_report), patch(
        "beginner_frontend.app.collect_asset_checks",
        return_value=network_fail_assets,
    ), patch("beginner_frontend.app.collect_custom_node_checks", return_value=network_fail_nodes), patch(
        "beginner_frontend.app.run_logged_command",
        side_effect=fail_if_command_runs,
    ):
        frontend.run_full_setup_worker(
            network_fail_job_id,
            backend="skip",
            profile="post-only",
            base_dir=ROOT,
            dry_run=False,
            skip_comfy_install=True,
            use_local_comfy_python=False,
        )
    network_fail_job = frontend.INSTALL_JOBS.pop(network_fail_job_id)
    check(network_fail_job["status"] == "failed", "full setup should fail early when download network preflight fails")
    check(network_fail_job.get("failure_hint", {}).get("category") == "network", "network preflight failures should use network failure hints")
    no_hosts_job = {"id": "self_check_no_download_hosts", "log": []}
    frontend.run_download_network_preflight(no_hosts_job, set())
    check(no_hosts_job.get("network_preflight", {}).get("status") == "skipped", "download network preflight should skip when nothing needs downloading")
    check(
        frontend.required_download_hosts_for_comfy_install("skip") == {"github.com", "pypi.org"}
        and "download.pytorch.org" in frontend.required_download_hosts_for_comfy_install("cuda"),
        "standalone ComfyUI install should preflight GitHub/PyPI/PyTorch hosts according to backend",
    )
    with patch("beginner_frontend.app.COMFYUI_EFFECTIVE_BACKEND", return_value="mps"):
        check(
            frontend.required_download_hosts_for_comfy_install("auto") == {"github.com", "pypi.org"},
            "macOS/MPS ComfyUI install should not preflight download.pytorch.org because torch uses the configured PyPI source",
        )
    with patch("beginner_frontend.app.COMFYUI_EFFECTIVE_BACKEND", return_value="cpu"):
        check(
            "download.pytorch.org" in frontend.required_download_hosts_for_comfy_install("auto"),
            "CPU ComfyUI install should still preflight the PyTorch wheel host",
        )

    ready_assets = [
        {"id": "rife49", "ok": True, "download_host": "huggingface.co"},
        {"id": "realesrgan_x2", "ok": True, "download_host": "github.com"},
        {"id": "ultrasharp_x4", "ok": True, "download_host": "huggingface.co"},
        {"id": "sample_keyframe", "ok": True, "download_host": ""},
    ]
    ready_nodes = [
        {"id": "video_helper_suite", "ok": True, "download_host": "github.com"},
        {"id": "frame_interpolation", "ok": True, "download_host": "github.com"},
    ]
    with patch("beginner_frontend.app.collect_asset_checks", return_value=ready_assets), patch("beginner_frontend.app.collect_custom_node_checks", return_value=ready_nodes):
        ready_hosts = frontend.required_download_hosts_for_full_setup(
            profile="post-only",
            base_dir=ROOT,
            backend="skip",
            skip_comfy_install=True,
            use_local_comfy_python=False,
        )
    check(ready_hosts == set(), "full setup should not preflight download hosts when selected assets and nodes are already ready")
    missing_asset_and_node_assets = [
        {"id": "rife49", "ok": False, "download_host": "huggingface.co"},
        {"id": "realesrgan_x2", "ok": True, "download_host": "github.com"},
        {"id": "ultrasharp_x4", "ok": True, "download_host": "huggingface.co"},
        {"id": "sample_keyframe", "ok": True, "download_host": ""},
    ]
    missing_asset_and_node_nodes = [
        {"id": "video_helper_suite", "ok": True, "download_host": "github.com"},
        {"id": "frame_interpolation", "ok": False, "download_host": "github.com"},
    ]
    with patch("beginner_frontend.app.collect_asset_checks", return_value=missing_asset_and_node_assets), patch(
        "beginner_frontend.app.collect_custom_node_checks",
        return_value=missing_asset_and_node_nodes,
    ):
        asset_install_hosts = frontend.required_download_hosts_for_workflow_assets(
            profile="post-only",
            base_dir=ROOT,
            use_local_comfy_python=True,
        )
    check(
        {"huggingface.co", "github.com", "pypi.org"} <= asset_install_hosts and "download.pytorch.org" not in asset_install_hosts,
        "workflow asset install should preflight missing asset/node hosts and node requirement PyPI, but not PyTorch",
    )
    with patch("beginner_frontend.app.collect_asset_checks", return_value=ready_assets), patch(
        "beginner_frontend.app.collect_custom_node_checks",
        return_value=ready_nodes,
    ), patch("beginner_frontend.app.COMFYUI_EFFECTIVE_BACKEND", return_value="mps"):
        mps_hosts = frontend.required_download_hosts_for_full_setup(
            profile="post-only",
            base_dir=ROOT,
            backend="auto",
            skip_comfy_install=False,
            use_local_comfy_python=False,
        )
    check(
        {"github.com", "pypi.org"}.issubset(mps_hosts) and "download.pytorch.org" not in mps_hosts,
        "Mac/MPS full setup should require GitHub/PyPI but not the PyTorch wheel host",
    )

    def fake_nonrequired_network_report(*args: object, **kwargs: object) -> dict:
        check(kwargs.get("include_network") is True, "download network preflight should request network details")
        return {
            "checks": [
                {
                    "id": "network",
                    "label": "下载网络",
                    "status": "warn",
                    "blocking": False,
                    "message": "部分下载站点不可连接：PyPI",
                    "action": "检查代理、DNS、防火墙或稍后重试。",
                    "reachable": {"github.com": True, "huggingface.co": True, "pypi.org": False, "download.pytorch.org": True},
                }
            ]
        }

    nonrequired_fail_job = {"id": "self_check_nonrequired_network_fail", "log": []}
    with patch("beginner_frontend.app.BUILD_PREREQUISITE_REPORT", side_effect=fake_nonrequired_network_report), patch(
        "beginner_frontend.app.env_hf_endpoint",
        return_value="",
    ), patch("beginner_frontend.app.saved_hf_endpoint", return_value=""), patch(
        "beginner_frontend.app.env_pip_index_url",
        return_value="",
    ), patch("beginner_frontend.app.saved_pip_index_url", return_value=""):
        frontend.run_download_network_preflight(nonrequired_fail_job, {"github.com", "huggingface.co"})
    check(
        nonrequired_fail_job.get("network_preflight", {}).get("missing_required_hosts") == [],
        "download network preflight should ignore unreachable hosts that are not needed for the selected install profile",
    )
    with tempfile.TemporaryDirectory(prefix="wan22_install_dir_check_") as temp_name:
        temp_root = Path(temp_name)
        base_dir = temp_root / "base"
        alien_install = base_dir / "ComfyUI"
        project_dir = temp_root / "project"
        alien_install.mkdir(parents=True)
        project_dir.mkdir()
        (alien_install / "not_comfy.txt").write_text("occupied", encoding="utf-8")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("COMFY_INSTALL_DIR", None)
            with patch("beginner_frontend.app.WORKSPACE_DIR", project_dir):
                check(
                    frontend.default_comfy_install_dir(base_dir) == project_dir / "ComfyUI",
                    "default ComfyUI install dir should fall back to project-local dir when base ComfyUI is occupied",
                )
        with patch.dict(os.environ, {"COMFY_INSTALL_DIR": str(alien_install)}, clear=False):
            check(
                frontend.default_comfy_install_dir(base_dir) == alien_install,
                "explicit COMFY_INSTALL_DIR should be respected even when occupied",
            )
    with tempfile.TemporaryDirectory(prefix="comfy_desktop_runtime_") as temp_name:
        temp_root = Path(temp_name)
        base_dir = temp_root / "base"
        project_dir = temp_root / "project"
        desktop_root = base_dir / "ComfyUI"
        bundled_main = desktop_root / "resources" / "ComfyUI" / "main.py"
        base_python = frontend.venv_python_for(base_dir / ".venv")
        bundled_main.parent.mkdir(parents=True)
        base_python.parent.mkdir(parents=True)
        bundled_main.write_text("# fake desktop ComfyUI", encoding="utf-8")
        base_python.write_text("", encoding="utf-8")
        project_dir.mkdir()
        if frontend.platform.system() == "Darwin":
            (desktop_root / "ComfyUI.app").mkdir(parents=True)
        else:
            (desktop_root / ("ComfyUI.exe" if frontend.platform.system() == "Windows" else "ComfyUI")).write_text("", encoding="utf-8")
        with patch("beginner_frontend.app.BASE_DIR", base_dir), patch(
            "beginner_frontend.app.WORKSPACE_DIR",
            project_dir,
        ), patch("beginner_frontend.app.COMFY_INSTALL_DIR", project_dir / "ComfyUI"):
            runtime = frontend.comfy_runtime_info()
            check(runtime["kind"] == "desktop_runtime", "ComfyUI Desktop bundled source should be detected as a startable runtime")
            check(Path(str(runtime["main_py"])) == bundled_main.resolve(), "Desktop runtime should use bundled ComfyUI main.py")
            check(Path(str(runtime["python"])) == base_python.resolve(), "Desktop runtime should use the base .venv Python")
            bootstrap = frontend.bootstrap_status_payload(False)
            check(bootstrap["can_start_comfyui"] is True and bootstrap["runtime_source_ready"] is True, "bootstrap should allow starting a detected Desktop runtime")
            start_command = frontend.comfy_start_command()
            check(str(bundled_main.resolve()) in start_command and str(base_python.resolve()) in start_command, "start command should target Desktop runtime paths")
        with patch.dict(os.environ, {"COMFY_INSTALL_DIR": ""}, clear=False):
            doctor_install = prerequisite_doctor.default_install_dir(base_dir)
        doctor_check = prerequisite_doctor.check_comfy_install_dir(doctor_install)
        check(doctor_install == desktop_root.resolve(), "prerequisite doctor should default to the parent ComfyUI Desktop directory")
        check(doctor_check["status"] == "ok" and doctor_check.get("runtime") == "desktop", "prerequisite doctor should not block ComfyUI Desktop runtime directories")
    ffmpeg_probe = frontend.ffmpeg_probe()
    check("name" in ffmpeg_probe and "install_hint" in ffmpeg_probe, "ffmpeg probe payload incomplete")
    ffmpeg_tools = frontend.ffmpeg_tool_checks()
    check({item["name"] for item in ffmpeg_tools} == {"ffmpeg", "ffmpeg:deflicker", "ffmpeg:hqdn3d"}, "ffmpeg tool checks incomplete")
    connect_error_text = frontend.comfy_connection_error_text(frontend.httpx.ConnectError(""))
    check("连接被拒绝" in connect_error_text, "empty ComfyUI connection errors should become beginner-friendly text")
    with tempfile.TemporaryDirectory(prefix="comfy_argv_base_") as temp_name:
        comfy_root = Path(temp_name) / "ComfyUI"
        comfy_root.mkdir()
        main_py = comfy_root / "main.py"
        main_py.write_text("# fake", encoding="utf-8")
        inferred_paths = frontend.comfy_paths_from_system_stats({"system": {"argv": [str(main_py)]}})
        check(inferred_paths["source"] == "running_comfyui", "connected ComfyUI without --base-directory should still be treated as running")
        check(inferred_paths["base_dir_source"] == "argv_main_py", "ComfyUI base source should record argv main.py inference")
        check(Path(inferred_paths["base_dir"]) == comfy_root.resolve(), "ComfyUI base should be inferred from argv main.py when --base-directory is absent")
        serialized_paths = frontend.serializable_comfy_paths(inferred_paths)
        check(serialized_paths["main_py"] == str(main_py.resolve()), "serialized ComfyUI paths should include inferred main.py")
    check(install_comfyui.COMFY_ZIP_CANDIDATES, "ComfyUI zip fallback URLs missing")
    install_comfyui_text = (ROOT / "scripts" / "install_comfyui.py").read_text(encoding="utf-8")
    install_assets_text = (ROOT / "scripts" / "install_workflow_assets.py").read_text(encoding="utf-8")
    check("dest.name + \".part\"" in install_comfyui_text and "retries: int = 3" in install_comfyui_text, "ComfyUI zip fallback should retry via .part files")
    check("dest.name + \".part\"" in install_assets_text and "retries: int = 3" in install_assets_text, "custom node zip fallback should retry via .part files")
    check("ensure_pip_available" in install_comfyui_text and "ensurepip" in install_comfyui_text and "run_pip" in install_comfyui_text, "ComfyUI installer should repair missing pip and retry dependency installs")
    check("ensure_pip_available" in install_assets_text and "ensurepip" in install_assets_text and "run_pip" in install_assets_text, "workflow asset installer should repair missing pip and retry custom node dependency installs")
    check("Install target is not empty; refusing to replace it" in install_comfyui_text and "dest.rmdir()" in install_comfyui_text, "ComfyUI zip fallback should only replace an empty target directory")
    check("Custom node target is not empty; refusing to replace it" in install_assets_text and "dest.rmdir()" in install_assets_text, "custom node zip fallback should only replace an empty target directory")
    check("git clone failed" in install_comfyui_text and "install_repo_from_zip(install_dir)" in install_comfyui_text, "ComfyUI install should fall back to source zip when git clone fails")
    check("git clone failed" in install_assets_text and "install_node_from_zip" in install_assets_text, "custom node install should fall back to source zip when git clone fails")
    comfy_pip_calls: list[list[str]] = []

    def flaky_comfy_pip(command: list[str], cwd: Path | None = None) -> None:
        del cwd
        comfy_pip_calls.append(command)
        if len(comfy_pip_calls) == 1:
            raise subprocess.CalledProcessError(1, command)

    with patch("scripts.install_comfyui.run", side_effect=flaky_comfy_pip), patch("scripts.install_comfyui.time.sleep", return_value=None):
        install_comfyui.run_pip([str(Path("python")), "-m", "pip", "install", "torch"])
    check(len(comfy_pip_calls) == 2, "ComfyUI installer pip commands should retry after a transient failure")
    assets_pip_calls: list[list[str]] = []

    def flaky_assets_pip(command: list[str], cwd: Path) -> None:
        del cwd
        assets_pip_calls.append(command)
        if len(assets_pip_calls) == 1:
            raise subprocess.CalledProcessError(1, command)

    with patch("scripts.install_workflow_assets.run", side_effect=flaky_assets_pip), patch("scripts.install_workflow_assets.time.sleep", return_value=None):
        install_workflow_assets.run_pip([str(Path("python")), "-m", "pip", "install", "opencv-python"], ROOT)
    check(len(assets_pip_calls) == 2, "workflow asset installer pip commands should retry after a transient failure")
    with tempfile.TemporaryDirectory(prefix="comfy_clone_fallback_") as temp_name:
        install_dir = Path(temp_name) / "ComfyUI"

        def fail_comfy_clone(command: list[str], cwd: Path | None = None) -> None:
            del cwd
            install_dir.mkdir(parents=True, exist_ok=True)
            (install_dir / "partial.txt").write_text("partial", encoding="utf-8")
            raise subprocess.CalledProcessError(128, command)

        def fake_comfy_zip(dest: Path) -> None:
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "main.py").write_text("# zip fallback", encoding="utf-8")

        with patch("scripts.install_comfyui.shutil.which", return_value="git"), patch("scripts.install_comfyui.run", side_effect=fail_comfy_clone), patch(
            "scripts.install_comfyui.install_repo_from_zip",
            side_effect=fake_comfy_zip,
        ):
            install_comfyui.ensure_repo(install_dir)
        check((install_dir / "main.py").exists() and not (install_dir / "partial.txt").exists(), "ComfyUI git clone failure should clean partial target and use zip fallback")
    with tempfile.TemporaryDirectory(prefix="node_clone_fallback_") as temp_name:
        base_dir = Path(temp_name)
        node_dest = base_dir / "custom_nodes" / "FakeNode"

        def fail_node_clone(command: list[str], cwd: Path) -> None:
            del cwd
            node_dest.mkdir(parents=True, exist_ok=True)
            (node_dest / "partial.txt").write_text("partial", encoding="utf-8")
            raise subprocess.CalledProcessError(128, command)

        def fake_node_zip(repo_url: str, dest: Path) -> None:
            del repo_url
            dest.mkdir(parents=True, exist_ok=True)
            (dest / "__init__.py").write_text("# zip fallback", encoding="utf-8")

        fake_repos = [{"id": "fake_node", "name": "FakeNode", "url": "https://github.com/example/FakeNode.git", "dest": "custom_nodes/FakeNode"}]
        with patch("scripts.install_workflow_assets.CUSTOM_NODE_REPOS", fake_repos), patch(
            "scripts.install_workflow_assets.shutil.which",
            return_value="git",
        ), patch("scripts.install_workflow_assets.run", side_effect=fail_node_clone), patch(
            "scripts.install_workflow_assets.install_node_from_zip",
            side_effect=fake_node_zip,
        ):
            install_workflow_assets.ensure_custom_nodes(base_dir, install_requirements=False)
        check((node_dest / "__init__.py").exists() and not (node_dest / "partial.txt").exists(), "custom node git clone failure should clean partial target and use zip fallback")
    with patch("scripts.install_comfyui.platform.system", return_value="Darwin"):
        check(install_comfyui.effective_backend("auto") == "mps", "macOS auto backend should use MPS")
    with patch("scripts.install_comfyui.platform.system", return_value="Windows"), patch("scripts.install_comfyui.shutil.which", return_value=None):
        check(install_comfyui.effective_backend("auto") == "cpu", "Windows without NVIDIA should use CPU backend")
    fake_nvidia = subprocess.CompletedProcess(["nvidia-smi", "-L"], 0, stdout="GPU 0: NVIDIA RTX PRO 5000", stderr="")
    with patch("scripts.install_comfyui.platform.system", return_value="Windows"), patch("scripts.install_comfyui.shutil.which", return_value="nvidia-smi"), patch("scripts.install_comfyui.subprocess.run", return_value=fake_nvidia):
        check(install_comfyui.effective_backend("auto") == "cuda", "Windows with NVIDIA should use CUDA backend")
    comfy_disk_plan = install_comfyui.comfyui_disk_plan(ROOT / "ComfyUI", "skip")
    check("ok" in comfy_disk_plan and "recommended_free_gb" in comfy_disk_plan, "ComfyUI disk plan is incomplete")
    with tempfile.TemporaryDirectory(prefix="comfy_self_check_") as temp_name:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "install_comfyui.py"),
                "--base-dir",
                str(Path(temp_name) / "base"),
                "--install-dir",
                str(Path(temp_name) / "ComfyUI"),
                "--backend",
                "skip",
                "--dry-run",
            ],
            capture_output=True,
            text=True,
        )
    check(result.returncode == 0, "ComfyUI installer dry-run failed: " + result.stderr + result.stdout)
    check(install_workflow_assets.github_zip_candidates("https://github.com/a/b.git")[0].endswith("/main.zip"), "node zip URL fallback failed")
    with tempfile.TemporaryDirectory(prefix="wan22_local_asset_") as temp_name:
        temp_root = Path(temp_name)
        cache_root = temp_root / "cache"
        base_dir = temp_root / "base"
        payload = b"local asset payload"
        fake_item = {
            "id": "fake_local_asset",
            "group": "post",
            "name": "fake_local_asset.safetensors",
            "url": "https://huggingface.co/example/fake/resolve/main/fake_local_asset.safetensors",
            "dest": "models/checkpoints/fake_local_asset.safetensors",
            "bytes": len(payload),
        }
        local_file = cache_root / "models" / "checkpoints" / "fake_local_asset.safetensors"
        local_file.parent.mkdir(parents=True)
        local_file.write_bytes(payload)
        target = base_dir / str(fake_item["dest"])
        with patch.dict(os.environ, {"WAN22_LOCAL_ASSET_DIRS": str(cache_root), "WAN22_COPY_LOCAL_ASSETS": "1"}, clear=False):
            check(
                install_workflow_assets.find_local_asset_candidate(fake_item, dest=target, base_dir=base_dir) == local_file.resolve(),
                "installer should discover matching files from WAN22_LOCAL_ASSET_DIRS",
            )
            check(
                install_workflow_assets.remaining_bytes_for_item(base_dir, fake_item) == 0,
                "local cache hits should not count as network download bytes",
            )
            check(
                install_workflow_assets.install_from_local_asset(fake_item, target, len(payload), base_dir) is True,
                "installer should copy/link a matching local asset before downloading",
            )
        check(target.read_bytes() == payload, "local asset copy/link wrote the wrong file")
        fake_frontend_asset = {
            "id": "rife49",
            "label": "Fake local RIFE",
            "step": "RIFE 插帧",
            "path": base_dir / str(fake_item["dest"]),
            "bytes": len(payload),
            "dest": str(fake_item["dest"]),
            "name": str(fake_item["name"]),
            "url": str(fake_item["url"]),
            "installable": True,
        }
        target.unlink()
        with patch.dict(os.environ, {"WAN22_LOCAL_ASSET_DIRS": str(cache_root)}, clear=False), patch(
            "beginner_frontend.app.workflow_asset_manifest",
            return_value=[fake_frontend_asset],
        ):
            local_check = frontend.collect_asset_checks(base_dir)[0]
            check(local_check["local_available"] is True and local_check["remaining_gb"] == 0, "frontend should surface reusable local assets")
            with patch("beginner_frontend.app.collect_asset_checks", return_value=[local_check]), patch(
                "beginner_frontend.app.collect_custom_node_checks",
                return_value=[],
            ):
                hosts = frontend.required_download_hosts_for_workflow_assets(
                    profile="post-only",
                    base_dir=base_dir,
                    use_local_comfy_python=False,
                )
            check("huggingface.co" not in hosts, "local asset hits should not force Hugging Face network preflight")
    with tempfile.TemporaryDirectory(prefix="wan22_self_check_") as temp_name:
        result = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "install_workflow_assets.py"),
                "--base-dir",
                temp_name,
                "--profile",
                "post-only",
                "--dry-run",
                "--no-custom-nodes",
                "--no-node-requirements",
            ],
            capture_output=True,
            text=True,
        )
    check(result.returncode == 0, "installer dry-run failed: " + result.stderr + result.stdout)
    check(launcher.frontend_url_for_port(7861).endswith(":7861"), "launcher port helper failed")
    return {"zip_fallback": True, "launcher_version": launcher.APP_VERSION}


def download_resume_check() -> dict[str, bool]:
    sys.path.insert(0, str(ROOT))
    from beginner_frontend import app as frontend
    from scripts import install_workflow_assets

    class FakeResponse:
        def __init__(self, data: bytes, status: int = 206) -> None:
            self.data = data
            self.status = status
            self.offset = 0

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self, size: int = -1) -> bytes:
            if self.offset >= len(self.data):
                return b""
            if size < 0:
                size = len(self.data) - self.offset
            chunk = self.data[self.offset : self.offset + size]
            self.offset += len(chunk)
            return chunk

    payload = bytes((index % 251 for index in range(257 * 1024)))
    split_at = 41_000
    seen_ranges: list[str | None] = []

    def fake_urlopen(request: object, timeout: int = 60) -> FakeResponse:
        del timeout
        range_header = request.get_header("Range") if hasattr(request, "get_header") else None
        seen_ranges.append(range_header)
        check(range_header == f"bytes={split_at}-", "download resume should request the remaining byte range")
        return FakeResponse(payload[split_at:], status=206)

    with tempfile.TemporaryDirectory(prefix="wan22_resume_check_") as temp_name:
        dest = Path(temp_name) / "model.safetensors"
        partial = dest.with_name(dest.name + ".part")
        partial.write_bytes(payload[:split_at])
        with patch("scripts.install_workflow_assets.urllib.request.urlopen", side_effect=fake_urlopen):
            with contextlib.redirect_stdout(io.StringIO()):
                install_workflow_assets.download_file("https://example.invalid/model", dest, len(payload), retries=1)
        check(dest.read_bytes() == payload, "resumed download should produce exact file contents")
        check(not partial.exists(), "partial file should be replaced after a complete resumed download")

    with tempfile.TemporaryDirectory(prefix="wan22_complete_part_check_") as temp_name:
        dest = Path(temp_name) / "model.safetensors"
        partial = dest.with_name(dest.name + ".part")
        partial.write_bytes(payload)
        with patch("scripts.install_workflow_assets.urllib.request.urlopen", side_effect=AssertionError("network should not be used for complete .part")):
            with contextlib.redirect_stdout(io.StringIO()):
                install_workflow_assets.download_file("https://example.invalid/model", dest, len(payload), retries=1)
        check(dest.read_bytes() == payload, "complete .part should be finalized without a network request")
        check(not partial.exists(), "complete .part should be renamed to the final model file")

    with tempfile.TemporaryDirectory(prefix="wan22_partial_ui_check_") as temp_name:
        base_dir = Path(temp_name)
        first_asset = frontend.workflow_asset_manifest(base_dir)[0]
        partial = Path(first_asset["path"]).with_name(Path(first_asset["path"]).name + ".part")
        partial.parent.mkdir(parents=True, exist_ok=True)
        partial.write_bytes(b"partial")
        asset = next(item for item in frontend.collect_asset_checks(base_dir) if item["id"] == first_asset["id"])
        check(asset["partial_exists"] is True, "frontend asset checks should expose .part files")
        check(asset["remaining_gb"] is not None, "frontend asset checks should expose remaining download size")
        check("断点续传" in asset["reason"], "frontend asset reason should explain resumable partial downloads")
        tiny_path = base_dir / "models" / "tiny.bin"
        tiny_asset = {
            "id": "tiny_complete_part",
            "label": "Tiny complete part",
            "step": "self-check",
            "path": tiny_path,
            "bytes": len(payload),
            "installable": True,
        }
        tiny_partial = tiny_path.with_name(tiny_path.name + ".part")
        tiny_partial.parent.mkdir(parents=True, exist_ok=True)
        tiny_partial.write_bytes(payload)
        with patch("beginner_frontend.app.workflow_asset_manifest", return_value=[tiny_asset]):
            complete_part_asset = next(item for item in frontend.collect_asset_checks(base_dir) if item["id"] == "tiny_complete_part")
        check(complete_part_asset["remaining_gb"] == 0, "complete .part should show zero remaining download")
        check("自动转正" in complete_part_asset["reason"], "frontend asset reason should explain complete .part finalization")

    return {"range_resume": True, "partial_ui_state": True}


def service_mode_check() -> dict[str, object]:
    sys.path.insert(0, str(ROOT))
    from fastapi.testclient import TestClient
    from beginner_frontend import app as frontend

    with tempfile.TemporaryDirectory(prefix="wan22_service_mode_") as temp_name:
        temp_config = Path(temp_name) / ".wan22_workflow_config.json"
        with patch.object(frontend, "LOCAL_CONFIG_FILE", temp_config):
            client = TestClient(frontend.app)
            config_response = client.get("/api/client-config")
            check(config_response.status_code == 200, "/api/client-config should be available")
            initial_service = config_response.json()["service"]
            check(initial_service["mode"] == "both", "unconfigured service mode should default to both")
            check(initial_service["first_run_required"] is True, "unconfigured service mode should prompt first-run selection")
            check(initial_service["bind_host"] == "127.0.0.1", "unconfigured service mode should keep the launcher local-only")
            check(initial_service["binds_lan"] is False, "unconfigured service mode should not advertise LAN binding")

            first_save = client.post("/api/service-config", json={"service": {"mode": "both"}})
            check(first_save.status_code == 200, "saving initial both mode should work")
            saved_service = first_save.json()["service"]
            token = saved_service.get("access_token")
            check(saved_service["bind_host"] == "0.0.0.0", "both mode should bind LAN after restart")
            check(bool(token), "server/both mode should create an access token")

            denied = client.post("/api/service-config", json={"service": {"mode": "server"}})
            check(denied.status_code == 401, "remote write should require a token after token is configured")

            headers = {"X-WAN22-Token": token}
            server_save = client.post("/api/service-config", json={"service": {"mode": "server", "access_token": token}}, headers=headers)
            check(server_save.status_code == 200, "saving server mode with token should work")
            check(server_save.json()["service"]["bind_host"] == "0.0.0.0", "server mode should bind LAN after restart")

            client_save = client.post(
                "/api/service-config",
                json={"service": {"mode": "client", "server_url": "http://192.168.1.20:7860", "access_token": token}},
                headers=headers,
            )
            check(client_save.status_code == 200, "saving client mode with token should work")
            client_service = client_save.json()["service"]
            check(client_service["bind_host"] == "127.0.0.1", "client mode should stay local-only")
            check(client_service["api_base_url"] == "http://192.168.1.20:7860", "client mode should expose remote API base URL")
            health_response = client.get("/api/health")
            check("service" in health_response.json(), "/api/health should expose service metadata")

    frontend_js_text = (ROOT / "beginner_frontend" / "static" / "app.js").read_text(encoding="utf-8")
    index_text = (ROOT / "beginner_frontend" / "static" / "index.html").read_text(encoding="utf-8")
    launcher_text = (ROOT / "START_WORKFLOW.py").read_text(encoding="utf-8")
    check("servicePanel" in index_text and "运行入口模式" in index_text, "frontend should render a first-run service panel")
    check("rewriteApiUrl" in frontend_js_text and "/api/client-config" in frontend_js_text, "frontend should route API calls through selected service mode")
    check("mediaUrl(item.url)" in frontend_js_text, "remote client mode should render media links against the service API")
    check("保存并重启后显示" in frontend_js_text, "first-run service panel should not show LAN URL before the mode is saved")
    check("frontend_bind_host" in launcher_text and "WAN22_NODE_MODE" in launcher_text, "launcher should honor saved service mode when choosing bind host")
    return {"modes": ["both", "server", "client"], "token_required": True}


def package_check() -> dict[str, int]:
    check(PACKAGE.exists(), "github_upload package directory missing")
    bad: list[str] = []
    local_path_hits: list[str] = []
    for path in PACKAGE.rglob("*"):
        if POLLUTION_RE.search(path.name):
            bad.append(rel(path))
        if not path.is_file() or path.name == "self_check.py" or path.suffix.lower() not in TEXT_SCAN_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in LOCAL_PATH_PATTERNS:
            if pattern.search(text):
                local_path_hits.append(f"{rel(path)} contains {label}")
    check(not bad, "release package contains local/generated artifacts: " + ", ".join(bad[:20]))
    check(not local_path_hits, "release package contains local machine paths: " + "; ".join(local_path_hits[:20]))
    required = [
        PACKAGE / "README.md",
        PACKAGE / "START_HERE.md",
        PACKAGE / "BEGINNER_FRONTEND_GUIDE.md",
        PACKAGE / "PROJECT_REVIEW.md",
        PACKAGE / "requirements.txt",
        PACKAGE / "START_WORKFLOW.bat",
        PACKAGE / "START_WORKFLOW.py",
        PACKAGE / "START_WORKFLOW.ps1",
        PACKAGE / "START_WORKFLOW.command",
        PACKAGE / "beginner_frontend" / "app.py",
        PACKAGE / "beginner_frontend" / "static" / "app.js",
        PACKAGE / "beginner_frontend" / "static" / "index.html",
        PACKAGE / "scripts" / "prerequisite_doctor.py",
        PACKAGE / "scripts" / "release_smoke.py",
        PACKAGE / "scripts" / "clean_bootstrap_smoke.py",
    ]
    missing = [rel(path) for path in required if not path.exists()]
    check(not missing, "release package missing required files: " + ", ".join(missing))
    batch_text = (PACKAGE / "START_WORKFLOW.bat").read_text(encoding="utf-8")
    check("ExecutionPolicy Bypass" in batch_text, "Windows batch launcher must bypass PowerShell execution policy")
    check("START_WORKFLOW.ps1" in batch_text, "Windows batch launcher must delegate to START_WORKFLOW.ps1")
    check("--check" in batch_text, "Windows batch launcher should expose a non-starting check mode")
    check("python START_WORKFLOW.py" in batch_text, "Windows batch launcher should explain no-PowerShell fallback")
    return {"files": sum(1 for _ in PACKAGE.rglob("*") if _.is_file())}


def keyword_check() -> list[str]:
    paths = [
        ROOT / "START_WORKFLOW.py",
        ROOT / "START_WORKFLOW.bat",
        ROOT / "START_WORKFLOW.ps1",
        ROOT / "START_WORKFLOW.command",
        ROOT / "beginner_frontend",
        ROOT / "scripts",
        ROOT / "workflows",
        ROOT / "BEGINNER_FRONTEND_GUIDE.md",
        PACKAGE,
    ]
    patterns = ["%date:", "frontend_7860", "No module named uvicorn", "旧入口仍可用", "只下载 LTX", "window.confirm", "Windows/Linux：默认走 CUDA"]
    hits: list[str] = []
    for path in paths:
        candidates = [path] if path.is_file() else [item for item in path.rglob("*") if item.is_file()]
        for item in candidates:
            if item.name == "self_check.py":
                continue
            try:
                text = item.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for pattern in patterns:
                if pattern in text:
                    hits.append(f"{rel(item)} contains {pattern}")
    check(not hits, "stale keyword hits: " + "; ".join(hits[:20]))
    return [f"{len(patterns)} stale patterns checked"]


def run_all() -> dict[str, object]:
    return {
        "python_ast": ast_check(),
        "javascript": js_check(),
        "manifest": manifest_check(),
        "workflows": workflow_check(),
        "hardware_matrix": hardware_matrix_check(),
        "fallbacks": fallback_check(),
        "downloads": download_resume_check(),
        "service_mode": service_mode_check(),
        "package": package_check(),
        "stale_keywords_absent": keyword_check(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run repository smoke checks for the local video workflow.")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args()
    try:
        result = run_all()
    except Exception as exc:
        if args.json:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2))
        else:
            print(f"SELF CHECK FAILED: {exc}")
        return 1
    if args.json:
        print(json.dumps({"ok": True, "result": result}, ensure_ascii=False, indent=2))
    else:
        print("SELF CHECK OK")
        for key, value in result.items():
            print(f"- {key}: {value}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
