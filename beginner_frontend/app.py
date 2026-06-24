from __future__ import annotations

import mimetypes
import json
import os
import platform
import random
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from fastapi import Body, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

try:
    from scripts.install_comfyui import comfyui_disk_plan as COMFYUI_DISK_PLAN
    from scripts.install_comfyui import effective_backend as COMFYUI_EFFECTIVE_BACKEND
except Exception:
    COMFYUI_DISK_PLAN = None
    COMFYUI_EFFECTIVE_BACKEND = None

try:
    from scripts.prerequisite_doctor import build_prerequisite_report as BUILD_PREREQUISITE_REPORT
except Exception:
    BUILD_PREREQUISITE_REPORT = None

try:
    from scripts.install_workflow_assets import CUSTOM_NODE_REPOS as INSTALL_CUSTOM_NODE_REPOS
    from scripts.install_workflow_assets import disk_space_plan as INSTALL_DISK_SPACE_PLAN
    from scripts.install_workflow_assets import find_local_asset_candidate as INSTALL_FIND_LOCAL_ASSET_CANDIDATE
    from scripts.install_workflow_assets import MODEL_FILES as INSTALL_MODEL_FILES
    from scripts.install_workflow_assets import planned_download_bytes as INSTALL_PLANNED_DOWNLOAD_BYTES
    from scripts.install_workflow_assets import selected_model_items as INSTALL_SELECTED_MODEL_ITEMS
except Exception:
    INSTALL_CUSTOM_NODE_REPOS = []
    INSTALL_MODEL_FILES = []
    INSTALL_DISK_SPACE_PLAN = None
    INSTALL_FIND_LOCAL_ASSET_CANDIDATE = None
    INSTALL_PLANNED_DOWNLOAD_BYTES = None
    INSTALL_SELECTED_MODEL_ITEMS = None


APP_DIR = Path(__file__).resolve().parent
WORKSPACE_DIR = APP_DIR.parent
APP_VERSION = os.environ.get("BEGINNER_FRONTEND_APP_VERSION", "2026.06.22.4")
STARTED_AT = time.time()
LOCAL_CONFIG_FILE = WORKSPACE_DIR / ".wan22_workflow_config.json"
HF_ENDPOINT_ENV_NAMES = ("WAN22_HF_ENDPOINT", "HF_ENDPOINT")
PROXY_ENV_NAMES = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY", "https_proxy", "http_proxy", "all_proxy")
PIP_INDEX_ENV_NAMES = ("PIP_INDEX_URL", "pip_index_url")
SERVICE_MODES = {"server", "client", "both"}


def looks_like_comfy_base(path: Path) -> bool:
    return all((path / name).exists() for name in ("models", "input", "output", "custom_nodes"))


def normalize_hf_endpoint(value: str | None) -> str:
    value = (value or "").strip().rstrip("/")
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("下载源必须是 http(s) URL，例如 https://huggingface.co")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"


def normalize_proxy_url(value: str | None) -> str:
    value = (value or "").strip().rstrip("/")
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("代理地址必须是 http(s) URL，例如 http://127.0.0.1:7890")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"


def normalize_pip_index_url(value: str | None) -> str:
    value = (value or "").strip().rstrip("/")
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("pip 镜像源必须是 http(s) URL，例如 https://pypi.org/simple")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"


def normalize_service_mode(value: str | None) -> str:
    mode = (value or "").strip().lower()
    return mode if mode in SERVICE_MODES else ""


def normalize_server_url(value: str | None) -> str:
    value = (value or "").strip().rstrip("/")
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("服务端地址必须是 http(s) URL，例如 http://192.168.1.20:7860")
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path.rstrip('/')}"


def redact_url(value: str | None) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    parsed = urlparse(value)
    if not parsed.netloc:
        return value
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    if parsed.username or parsed.password:
        netloc = f"***:***@{host}{port}"
    else:
        netloc = parsed.netloc
    return f"{parsed.scheme}://{netloc}{parsed.path or ''}" + (f"?{parsed.query}" if parsed.query else "")


def redact_sensitive_text(value: Any) -> str:
    text = str(value)
    return re.sub(r"(https?://)[^/\s:@]+:[^/\s@]+@", r"\1***:***@", text)


def read_local_config() -> dict[str, Any]:
    if not LOCAL_CONFIG_FILE.exists():
        return {}
    try:
        data = json.loads(LOCAL_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def write_local_config(config: dict[str, Any]) -> None:
    cleaned = {key: value for key, value in config.items() if value not in ("", None, [], {})}
    if not cleaned:
        LOCAL_CONFIG_FILE.unlink(missing_ok=True)
        return
    LOCAL_CONFIG_FILE.write_text(json.dumps(cleaned, ensure_ascii=False, indent=2), encoding="utf-8")


def saved_service_mode() -> str:
    return normalize_service_mode(str(read_local_config().get("node_mode") or ""))


def effective_service_mode() -> str:
    return normalize_service_mode(os.environ.get("WAN22_NODE_MODE")) or saved_service_mode() or "both"


def configured_access_token() -> str:
    return str(os.environ.get("WAN22_ACCESS_TOKEN") or read_local_config().get("access_token") or "").strip()


def get_lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except Exception:
        try:
            return socket.gethostbyname(socket.gethostname())
        except Exception:
            return ""


def request_origin(request: Request) -> str:
    return str(request.base_url).rstrip("/")


def service_url_payload(request: Request) -> dict[str, str]:
    parsed = urlparse(request_origin(request))
    port = f":{parsed.port}" if parsed.port else ""
    local_url = f"{parsed.scheme}://127.0.0.1{port}"
    lan_ip = get_lan_ip()
    lan_url = f"{parsed.scheme}://{lan_ip}{port}" if lan_ip else ""
    return {"local_url": local_url, "lan_url": lan_url, "lan_ip": lan_ip}


def service_config_payload(request: Request | None = None, *, include_sensitive: bool = False) -> dict[str, Any]:
    config = read_local_config()
    env_mode = normalize_service_mode(os.environ.get("WAN22_NODE_MODE"))
    saved_mode = normalize_service_mode(str(config.get("node_mode") or ""))
    mode_configured = bool(env_mode or saved_mode)
    mode = env_mode or saved_mode or "both"
    bind_host = (
        os.environ.get("BEGINNER_FRONTEND_BIND_HOST")
        or os.environ.get("BEGINNER_FRONTEND_HOST")
        or str(config.get("bind_host") or "")
        or ("0.0.0.0" if mode_configured and mode in {"server", "both"} else "127.0.0.1")
    )
    try:
        server_url = normalize_server_url(str(config.get("server_url") or ""))
    except ValueError:
        server_url = ""
    token = configured_access_token()
    urls = service_url_payload(request) if request else {"local_url": "", "lan_url": "", "lan_ip": ""}
    api_base_url = server_url if mode == "client" else ""
    payload: dict[str, Any] = {
        "mode": mode,
        "mode_configured": mode_configured,
        "first_run_required": not mode_configured,
        "mode_source": "environment" if env_mode else "saved" if saved_mode else "default",
        "bind_host": bind_host,
        "binds_lan": bind_host in {"0.0.0.0", "::"} or not bind_host.startswith("127."),
        "server_url": server_url,
        "api_base_url": api_base_url,
        "local_url": urls["local_url"],
        "lan_url": urls["lan_url"],
        "lan_ip": urls["lan_ip"],
        "access_token_configured": bool(token),
        "cors_enabled": True,
        "requires_restart_hint": "切换服务端/双模式后，需要重启 start.bat 才会监听局域网地址。",
    }
    if include_sensitive:
        payload["access_token"] = token
    return payload


def is_loopback_request(request: Request) -> bool:
    host = (request.client.host if request.client else "") or ""
    return host in {"127.0.0.1", "::1", "localhost"} or host.startswith("127.")


def env_hf_endpoint() -> str:
    for name in HF_ENDPOINT_ENV_NAMES:
        try:
            endpoint = normalize_hf_endpoint(os.environ.get(name))
        except ValueError:
            continue
        if endpoint:
            return endpoint
    return ""


def saved_hf_endpoint() -> str:
    try:
        return normalize_hf_endpoint(str(read_local_config().get("hf_endpoint") or ""))
    except ValueError:
        return ""


def effective_hf_endpoint() -> str:
    return env_hf_endpoint() or saved_hf_endpoint()


def env_proxy_url() -> str:
    for name in PROXY_ENV_NAMES:
        try:
            proxy_url = normalize_proxy_url(os.environ.get(name))
        except ValueError:
            continue
        if proxy_url:
            return proxy_url
    return ""


def saved_proxy_url() -> str:
    try:
        return normalize_proxy_url(str(read_local_config().get("proxy_url") or ""))
    except ValueError:
        return ""


def effective_proxy_url() -> str:
    return env_proxy_url() or saved_proxy_url()


def env_pip_index_url() -> str:
    for name in PIP_INDEX_ENV_NAMES:
        try:
            pip_index = normalize_pip_index_url(os.environ.get(name))
        except ValueError:
            continue
        if pip_index:
            return pip_index
    return ""


def saved_pip_index_url() -> str:
    try:
        return normalize_pip_index_url(str(read_local_config().get("pip_index_url") or ""))
    except ValueError:
        return ""


def effective_pip_index_url() -> str:
    return env_pip_index_url() or saved_pip_index_url()


def effective_download_url(url: str, hf_endpoint: str | None = None) -> str:
    endpoint = normalize_hf_endpoint(hf_endpoint) if hf_endpoint is not None else effective_hf_endpoint()
    if not endpoint:
        return url
    parsed = urlparse(url)
    if parsed.hostname != "huggingface.co":
        return url
    mirror = urlparse(endpoint)
    mirror_path = mirror.path.rstrip("/")
    path = f"{mirror_path}{parsed.path}" if mirror_path else parsed.path
    return f"{mirror.scheme}://{mirror.netloc}{path}" + (f"?{parsed.query}" if parsed.query else "")


def download_settings_payload(*, include_sensitive: bool = False) -> dict[str, Any]:
    env_endpoint = env_hf_endpoint()
    saved_endpoint = saved_hf_endpoint()
    effective = env_endpoint or saved_endpoint
    env_proxy = env_proxy_url()
    saved_proxy = saved_proxy_url()
    effective_proxy = env_proxy or saved_proxy
    env_pip_index = env_pip_index_url()
    saved_pip_index = saved_pip_index_url()
    effective_pip_index = env_pip_index or saved_pip_index
    return {
        "hf_endpoint": effective,
        "saved_hf_endpoint": saved_endpoint,
        "env_hf_endpoint": env_endpoint,
        "source": "environment" if env_endpoint else "saved" if saved_endpoint else "official",
        "proxy_url": effective_proxy if include_sensitive else redact_url(effective_proxy),
        "saved_proxy_url": saved_proxy if include_sensitive else redact_url(saved_proxy),
        "env_proxy_url": redact_url(env_proxy),
        "proxy_source": "environment" if env_proxy else "saved" if saved_proxy else "none",
        "pip_index_url": effective_pip_index,
        "saved_pip_index_url": saved_pip_index,
        "env_pip_index_url": env_pip_index,
        "pip_index_source": "environment" if env_pip_index else "saved" if saved_pip_index else "official",
        "official_pip_index_url": "https://pypi.org/simple",
        "sensitive": include_sensitive,
        "official_hf_endpoint": "https://huggingface.co",
        "config_file": str(LOCAL_CONFIG_FILE),
    }


def download_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    endpoint = effective_hf_endpoint()
    if endpoint:
        env["WAN22_HF_ENDPOINT"] = endpoint
    proxy_url = effective_proxy_url()
    if proxy_url:
        for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
            env[name] = proxy_url
    pip_index_url = effective_pip_index_url()
    if pip_index_url:
        env["PIP_INDEX_URL"] = pip_index_url
    return env


def call_with_download_env(func: Any, *args: Any, **kwargs: Any) -> Any:
    original = {name: os.environ.get(name) for name in (*HF_ENDPOINT_ENV_NAMES, *PROXY_ENV_NAMES, *PIP_INDEX_ENV_NAMES)}
    endpoint = effective_hf_endpoint()
    proxy_url = effective_proxy_url()
    pip_index_url = effective_pip_index_url()
    try:
        if endpoint:
            os.environ["WAN22_HF_ENDPOINT"] = endpoint
        if proxy_url:
            for name in ("HTTPS_PROXY", "HTTP_PROXY", "https_proxy", "http_proxy"):
                os.environ[name] = proxy_url
        if pip_index_url:
            os.environ["PIP_INDEX_URL"] = pip_index_url
        return func(*args, **kwargs)
    finally:
        for name, value in original.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def default_comfy_base_dir() -> Path:
    configured = os.environ.get("COMFY_BASE_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    parent = WORKSPACE_DIR.parent
    if looks_like_comfy_base(parent):
        return parent.resolve()
    return WORKSPACE_DIR.resolve()


def looks_like_comfy_install_dir(path: Path) -> bool:
    return (path / "main.py").exists() or (path / ".git").exists()


def install_dir_available(path: Path) -> bool:
    return not path.exists() or not any(path.iterdir()) or looks_like_comfy_install_dir(path)


def default_comfy_install_dir(base_dir: Path) -> Path:
    configured = os.environ.get("COMFY_INSTALL_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    preferred = (base_dir / "ComfyUI").expanduser().resolve()
    if install_dir_available(preferred):
        return preferred
    project_local = (WORKSPACE_DIR / "ComfyUI").expanduser().resolve()
    if project_local != preferred and install_dir_available(project_local):
        return project_local
    return preferred


BASE_DIR = default_comfy_base_dir()
INPUT_DIR = BASE_DIR / "input"
OUTPUT_DIR = BASE_DIR / "output"
TEMP_DIR = BASE_DIR / "temp"
UPLOAD_DIR = INPUT_DIR / "beginner_frontend"
STATIC_DIR = APP_DIR / "static"

COMFY_URL_CONFIGURED = bool(os.environ.get("COMFY_URL"))
COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8000").rstrip("/")
CLIENT_ID = f"beginner-frontend-{uuid.uuid4()}"
COMFY_INSTALL_DIR = default_comfy_install_dir(BASE_DIR)

DEFAULT_IMAGE = "wan22_sample_esports_keyframe.png"
DEFAULT_PROMPT = (
    "a realistic modern esports training room, Queensland esports branding style, "
    "soft RGB lighting, students using gaming peripherals, clean commercial photography, "
    "wide angle lens, natural shadows, cinematic slow dolly-in camera movement, "
    "polished commercial video"
)
DEFAULT_NEGATIVE = (
    "overexposed, low quality, blurry, jpeg artifacts, distorted hands, deformed face, "
    "extra fingers, warped screens, unreadable text, watermark, subtitles, flicker, "
    "jitter, chaotic background, NSFW"
)

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi"}
IMAGE_WORKFLOW_MODES = {"i2v_a14b", "ti2v_5b", "ltx_i2v"}

JOBS: dict[str, dict[str, Any]] = {}
INSTALL_JOBS: dict[str, dict[str, Any]] = {}
COMFY_PROCESS: subprocess.Popen[str] | None = None
COMFY_START_JOB_ID: str | None = None
COMFY_DISCOVERY_CACHE: dict[str, Any] = {"url": None, "checked_at": 0.0, "errors": []}
COMFY_DISCOVERY_SUCCESS_TTL = 15.0
COMFY_DISCOVERY_FAILURE_TTL = 4.0
COMFY_DISCOVERY_TIMEOUT = 1.5


app = FastAPI(title="Wan2.2 Beginner Frontend")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def require_remote_write_token(request: Request, call_next):
    token = configured_access_token()
    if (
        token
        and request.url.path.startswith("/api/")
        and request.method not in {"GET", "HEAD", "OPTIONS"}
        and not is_loopback_request(request)
    ):
        supplied = request.headers.get("X-WAN22-Token") or request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if supplied != token:
            return JSONResponse(
                status_code=401,
                content={
                    "detail": {
                        "message": "远程写入接口需要访问令牌",
                        "actions": ["在客户端服务面板填写服务端显示的访问令牌。"],
                    }
                },
            )
    return await call_next(request)


def configured_comfy_paths() -> dict[str, Any]:
    return {
        "base_dir": BASE_DIR,
        "input_dir": INPUT_DIR,
        "output_dir": OUTPUT_DIR,
        "temp_dir": TEMP_DIR,
        "user_dir": BASE_DIR / "user",
        "source": "configured",
    }


def path_from_arg(argv: list[Any], flag: str) -> str | None:
    for index, item in enumerate(argv):
        value = str(item)
        if value == flag and index + 1 < len(argv):
            return str(argv[index + 1])
        prefix = f"{flag}="
        if value.startswith(prefix):
            return value[len(prefix) :]
    return None


def infer_comfy_main_from_argv(argv: list[Any]) -> Path | None:
    for item in argv:
        value = str(item)
        if Path(value).name.lower() == "main.py":
            main_path = Path(value).expanduser()
            if main_path.is_absolute():
                return main_path.resolve()
    return None


def comfy_paths_from_system_stats(system_stats: dict[str, Any] | None) -> dict[str, Any]:
    paths = configured_comfy_paths()
    argv = ((system_stats or {}).get("system") or {}).get("argv") or []
    source = "running_comfyui" if system_stats is not None else "configured"
    base_dir_source = "configured"
    inferred_main = infer_comfy_main_from_argv(argv)
    if inferred_main:
        paths["main_py"] = inferred_main

    base_arg = path_from_arg(argv, "--base-directory")
    if base_arg:
        paths["base_dir"] = Path(base_arg).expanduser().resolve()
        base_dir_source = "argv_base_directory"
    elif system_stats is not None:
        if inferred_main:
            paths["base_dir"] = inferred_main.parent
            base_dir_source = "argv_main_py"
        else:
            base_dir_source = "configured_fallback"

    base_dir = Path(paths["base_dir"])
    for key, flag, fallback in [
        ("input_dir", "--input-directory", base_dir / "input"),
        ("output_dir", "--output-directory", base_dir / "output"),
        ("temp_dir", "--temp-directory", base_dir / "temp"),
        ("user_dir", "--user-directory", base_dir / "user"),
    ]:
        arg = path_from_arg(argv, flag)
        paths[key] = Path(arg).expanduser().resolve() if arg else fallback

    paths["source"] = source
    paths["base_dir_source"] = base_dir_source
    paths["base_dir_mismatch"] = Path(paths["base_dir"]).resolve() != BASE_DIR.resolve()
    return paths


def serializable_comfy_paths(paths: dict[str, Any]) -> dict[str, Any]:
    return {
        "configured_base_dir": str(BASE_DIR),
        "active_base_dir": str(paths["base_dir"]),
        "input_dir": str(paths["input_dir"]),
        "output_dir": str(paths["output_dir"]),
        "temp_dir": str(paths["temp_dir"]),
        "user_dir": str(paths["user_dir"]),
        "source": paths.get("source", "configured"),
        "base_dir_source": paths.get("base_dir_source", "configured"),
        "main_py": str(paths["main_py"]) if paths.get("main_py") else "",
        "base_dir_mismatch": bool(paths.get("base_dir_mismatch")),
    }


async def active_comfy_paths() -> dict[str, Any]:
    try:
        system_stats = await comfy_get("/system_stats")
    except HTTPException:
        return configured_comfy_paths()
    return comfy_paths_from_system_stats(system_stats)


def upload_dir_for(paths: dict[str, Any] | None = None) -> Path:
    return Path((paths or configured_comfy_paths())["input_dir"]) / "beginner_frontend"


def now_ms() -> int:
    return int(time.time() * 1000)


def common_comfy_urls() -> list[str]:
    urls = [COMFY_URL]
    if not COMFY_URL_CONFIGURED:
        urls.extend(
            [
                "http://127.0.0.1:8000",
                "http://127.0.0.1:8188",
                "http://localhost:8000",
                "http://localhost:8188",
            ]
        )
    unique: list[str] = []
    for url in urls:
        normalized = url.rstrip("/")
        if normalized not in unique:
            unique.append(normalized)
    return unique


async def request_comfy_json(
    method: str,
    base_url: str,
    path: str,
    payload: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> Any:
    timeout = timeout if timeout is not None else (60 if method == "POST" else 30)
    async with httpx.AsyncClient(timeout=timeout) as client:
        if method == "POST":
            response = await client.post(f"{base_url}{path}", json=payload)
        else:
            response = await client.get(f"{base_url}{path}")
        if response.status_code >= 400:
            try:
                detail = response.json()
            except ValueError:
                detail = response.text
            raise HTTPException(status_code=response.status_code, detail=detail)
        return response.json()


def comfy_connection_error_text(exc: BaseException | Any) -> str:
    if isinstance(exc, HTTPException):
        detail = exc.detail
        if isinstance(detail, (dict, list)):
            return json.dumps(detail, ensure_ascii=False)
        text = str(detail or "").strip()
        return text or "ComfyUI 返回了空错误。"
    if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.TimeoutException)):
        return "连接超时，请确认 ComfyUI 已启动且端口可访问。"
    if isinstance(exc, httpx.ConnectError):
        return "连接被拒绝，请先启动 ComfyUI，或检查端口是否为 8000/8188。"
    if isinstance(exc, ValueError):
        return "返回内容不是 ComfyUI JSON，请确认该端口运行的是 ComfyUI。"
    text = str(exc or "").strip()
    return text or exc.__class__.__name__


async def discover_comfy_url(force: bool = False) -> tuple[str | None, list[str]]:
    global COMFY_URL
    now = time.time()
    cached_url = COMFY_DISCOVERY_CACHE.get("url")
    checked_at = float(COMFY_DISCOVERY_CACHE.get("checked_at") or 0)
    if not force and cached_url and now - checked_at < COMFY_DISCOVERY_SUCCESS_TTL:
        return str(cached_url), []
    if not force and not cached_url and checked_at and now - checked_at < COMFY_DISCOVERY_FAILURE_TTL:
        return None, list(COMFY_DISCOVERY_CACHE.get("errors") or [])

    errors: list[str] = []
    for url in common_comfy_urls():
        try:
            await request_comfy_json("GET", url, "/system_stats", timeout=COMFY_DISCOVERY_TIMEOUT)
            COMFY_URL = url
            COMFY_DISCOVERY_CACHE.update({"url": url, "checked_at": time.time(), "errors": []})
            return url, []
        except HTTPException as exc:
            errors.append(f"{url}: {comfy_connection_error_text(exc)}")
        except (httpx.HTTPError, ValueError) as exc:
            errors.append(f"{url}: {comfy_connection_error_text(exc)}")
            continue
    COMFY_DISCOVERY_CACHE.update({"url": None, "checked_at": time.time(), "errors": errors})
    return None, errors


def safe_filename(name: str) -> str:
    suffix = Path(name).suffix.lower()
    stem = Path(name).stem
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem).strip("._-") or "upload"
    return f"{stem}_{uuid.uuid4().hex[:8]}{suffix}"


def clamp_int(value: int, low: int, high: int) -> int:
    return max(low, min(high, int(value)))


def clamp_float(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def normalize_frames(length: int) -> int:
    length = clamp_int(length, 17, 241)
    # Wan video latents use 1 + 4n frame counts.
    return 1 + 4 * round((length - 1) / 4)


def normalize_ltx_frames(length: int) -> int:
    length = clamp_int(length, 9, 241)
    # LTXV latent video nodes use 1 + 8n frame counts.
    return 1 + 8 * round((length - 1) / 8)


def normalize_dimension(value: int) -> int:
    value = clamp_int(value, 256, 1536)
    return max(32, round(value / 32) * 32)


def normalize_seed(seed: str | int | None) -> int:
    if seed in (None, "", "-1"):
        return random.randint(0, 2**63 - 1)
    try:
        parsed = int(seed)
    except ValueError:
        return random.randint(0, 2**63 - 1)
    if parsed < 0:
        return random.randint(0, 2**63 - 1)
    return parsed


def text_or_default(value: str | None, fallback: str) -> str:
    value = (value or "").strip()
    return value or fallback


def output_prefix(label: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", label).strip("._-") or "video"
    return f"wan22_frontend/{safe}_{time.strftime('%Y-%m-%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"


def clean_label(value: str | None) -> str:
    value = re.sub(r"\s+", " ", (value or "").strip())
    return value[:80]


def clean_id(value: str | None) -> str:
    return re.sub(r"[^A-Za-z0-9_.:-]+", "", (value or "").strip())[:80]


def workflow_profile_specs() -> dict[str, dict[str, Any]]:
    return {
        "wan22_ti2v_5b_720p": {
            "mode": "ti2v_5b",
            "label": "Wan2.2 TI2V-5B 720P",
        },
        "wan22_ti2v_5b_480p": {
            "mode": "ti2v_5b",
            "label": "Wan2.2 TI2V-5B 480P 小显存",
        },
        "wan22_i2v_a14b_720p": {
            "mode": "i2v_a14b",
            "label": "Wan2.2 I2V-A14B 720P",
        },
        "wan22_i2v_a14b_480p": {
            "mode": "i2v_a14b",
            "label": "Wan2.2 I2V-A14B 480P",
        },
        "wan22_t2v_a14b_720p": {
            "mode": "t2v_a14b",
            "label": "Wan2.2 T2V-A14B 720P",
        },
        "wan22_t2v_a14b_480p": {
            "mode": "t2v_a14b",
            "label": "Wan2.2 T2V-A14B 480P",
        },
        "wan22_ti2v_5b_final_720p": {
            "mode": "ti2v_5b",
            "label": "Wan2.2 TI2V-5B 720P 轻量正片",
        },
        "wan22_ti2v_5b_final_480p": {
            "mode": "ti2v_5b",
            "label": "Wan2.2 TI2V-5B 480P 小显存正片",
        },
        "mac_ltx_low_i2v": {
            "mode": "ltx_i2v",
            "label": "Mac LTX I2V 低档 384P",
        },
        "mac_ltx_balanced_i2v": {
            "mode": "ltx_i2v",
            "label": "Mac LTX I2V 均衡 512P",
        },
        "mac_ltx_quality_i2v": {
            "mode": "ltx_i2v",
            "label": "Mac LTX I2V 质量档",
        },
        "mac_ltx_low_t2v": {
            "mode": "ltx_t2v",
            "label": "Mac LTX T2V 低档",
        },
        "mac_ltx_balanced_t2v": {
            "mode": "ltx_t2v",
            "label": "Mac LTX T2V 均衡档",
        },
        "mac_wan5b_480p": {
            "mode": "ti2v_5b",
            "label": "Mac Wan2.2 TI2V-5B 480P 实验档",
        },
        "mac_wan5b_720p_experimental": {
            "mode": "ti2v_5b",
            "label": "Mac Wan2.2 TI2V-5B 720P 高内存实验档",
        },
        "no_local_video_draft": {
            "mode": "unsupported",
            "label": "当前硬件不建议本地生视频",
        },
        "no_local_video_final": {
            "mode": "unsupported",
            "label": "当前硬件不建议本地生视频",
        },
        "ffmpeg_deflicker_balanced": {
            "mode": "deflicker",
            "label": "ffmpeg deflicker + hqdn3d",
            "local": True,
        },
        "rife49_2x": {
            "mode": "rife_2x",
            "label": "RIFE 4.9 2x",
            "rife_multiplier": 2,
        },
        "rife49_4x": {
            "mode": "rife_2x",
            "label": "RIFE 4.9 4x",
            "rife_multiplier": 4,
        },
        "realesrgan_x2plus": {
            "mode": "upscale_2x",
            "label": "RealESRGAN x2plus 2x",
            "upscale_model": "RealESRGAN_x2plus.pth",
            "scale": 2,
        },
        "ultrasharp_4x": {
            "mode": "upscale_2x",
            "label": "4x-UltraSharp 4x",
            "upscale_model": "4x-UltraSharp.pth",
            "scale": 4,
        },
    }


def resolve_workflow_profile(
    *,
    model_profile: str | None,
    mode: str,
    model_label: str | None,
    upscale_model: str,
    rife_multiplier: int,
) -> dict[str, Any]:
    profile_id = clean_id(model_profile)
    spec = workflow_profile_specs().get(profile_id, {})
    resolved_mode = spec.get("mode", mode)
    resolved_label = clean_label(model_label) or spec.get("label", "")
    resolved_upscale = spec.get("upscale_model", upscale_model)
    resolved_rife = int(spec.get("rife_multiplier", rife_multiplier))
    return {
        "profile": profile_id,
        "mode": resolved_mode,
        "label": resolved_label,
        "upscale_model": resolved_upscale,
        "rife_multiplier": resolved_rife,
        "scale": spec.get("scale"),
        "local": bool(spec.get("local")),
    }


async def comfy_get(path: str) -> Any:
    global COMFY_URL
    discovered, errors = await discover_comfy_url()
    if discovered:
        try:
            result = await request_comfy_json("GET", discovered, path)
            COMFY_URL = discovered
            return result
        except HTTPException as exc:
            error = f"{discovered}: {comfy_connection_error_text(exc)}"
            COMFY_DISCOVERY_CACHE.update({"url": None, "checked_at": 0.0, "errors": [error]})
            errors = [error]
        except (httpx.HTTPError, ValueError) as exc:
            error = f"{discovered}: {comfy_connection_error_text(exc)}"
            COMFY_DISCOVERY_CACHE.update({"url": None, "checked_at": 0.0, "errors": [error]})
            errors = [error]
    raise HTTPException(
        status_code=503,
        detail=f"ComfyUI 连接失败。已尝试：{'; '.join(errors) or ', '.join(common_comfy_urls())}",
    )


async def comfy_post(path: str, payload: dict[str, Any]) -> Any:
    discovered, errors = await discover_comfy_url()
    if not discovered:
        raise HTTPException(
            status_code=503,
            detail=f"ComfyUI 连接失败。已尝试：{'; '.join(errors) or ', '.join(common_comfy_urls())}",
        )
    try:
        return await request_comfy_json("POST", discovered, path, payload)
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(
            status_code=503,
            detail=f"ComfyUI 连接失败：{comfy_connection_error_text(exc)}",
        ) from exc


def comfy_not_connected_detail(action: str, original_detail: Any) -> dict[str, Any]:
    return {
        "message": f"ComfyUI 未连接，无法{action}",
        "errors": [str(original_detail)],
        "actions": [
            "在第 1 步“环境侦测”点击“一键准备环境”。",
            "如果 ComfyUI 已安装但未运行，点击“启动 ComfyUI”，等待状态变成“已连接”。",
            "状态已连接后，先运行“生成链路测试”，通过后再生成正式镜头。",
        ],
    }


async def save_upload(upload: UploadFile, allowed: set[str], paths: dict[str, Any] | None = None) -> str:
    suffix = Path(upload.filename or "").suffix.lower()
    if suffix not in allowed:
        allowed_text = ", ".join(sorted(allowed))
        raise HTTPException(status_code=400, detail=f"文件格式不支持，请使用：{allowed_text}")

    upload_dir = upload_dir_for(paths)
    upload_dir.mkdir(parents=True, exist_ok=True)
    filename = unique_upload_filename(upload.filename or f"upload{suffix}")
    destination = upload_dir / filename
    with destination.open("wb") as output:
        shutil.copyfileobj(upload.file, output)
    return f"beginner_frontend/{filename}"


def unique_upload_filename(name: str) -> str:
    safe = safe_filename(name)
    path = Path(safe)
    suffix = path.suffix
    stem = path.stem or "upload"
    return f"{stem}_{time.strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}{suffix}"


def copy_media_to_input(filename: str, subfolder: str, file_type: str, paths: dict[str, Any] | None = None) -> str:
    source = safe_media_path(filename, subfolder, file_type, paths)
    if source.suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="上一阶段输出不是可处理的视频")

    upload_dir = upload_dir_for(paths)
    upload_dir.mkdir(parents=True, exist_ok=True)
    destination_name = unique_upload_filename(source.name)
    destination = upload_dir / destination_name
    shutil.copy2(source, destination)
    return f"beginner_frontend/{destination_name}"


def node(class_type: str, inputs: dict[str, Any]) -> dict[str, Any]:
    return {"class_type": class_type, "inputs": inputs}


def build_ti2v_prompt(
    *,
    prompt: str,
    negative: str,
    image_name: str,
    width: int,
    height: int,
    length: int,
    fps: int,
    seed: int,
    steps: int,
    cfg: float,
) -> dict[str, Any]:
    return {
        "1": node("LoadImage", {"image": image_name}),
        "2": node(
            "CLIPLoader",
            {
                "clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
                "type": "wan",
                "device": "default",
            },
        ),
        "3": node("VAELoader", {"vae_name": "wan2.2_vae.safetensors"}),
        "4": node(
            "UNETLoader",
            {
                "unet_name": "Wan2.2/wan2.2_ti2v_5B_fp16.safetensors",
                "weight_dtype": "default",
            },
        ),
        "5": node("ModelSamplingSD3", {"model": ["4", 0], "shift": 5.0}),
        "6": node("CLIPTextEncode", {"text": prompt, "clip": ["2", 0]}),
        "7": node("CLIPTextEncode", {"text": negative, "clip": ["2", 0]}),
        "8": node(
            "Wan22ImageToVideoLatent",
            {
                "vae": ["3", 0],
                "width": width,
                "height": height,
                "length": length,
                "batch_size": 1,
                "start_image": ["1", 0],
            },
        ),
        "9": node(
            "KSampler",
            {
                "model": ["5", 0],
                "seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "simple",
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["8", 0],
                "denoise": 1.0,
            },
        ),
        "10": node(
            "VAEDecodeTiled",
            {
                "samples": ["9", 0],
                "vae": ["3", 0],
                "tile_size": 512,
                "overlap": 64,
                "temporal_size": 16,
                "temporal_overlap": 4,
            },
        ),
        "11": node("CreateVideo", {"images": ["10", 0], "fps": fps}),
        "12": node(
            "SaveVideo",
            {
                "video": ["11", 0],
                "filename_prefix": output_prefix("TI2V_draft"),
                "format": "mp4",
                "codec": "h264",
            },
        ),
    }


def build_ltx_t2v_prompt(
    *,
    prompt: str,
    negative: str,
    width: int,
    height: int,
    length: int,
    fps: int,
    seed: int,
    steps: int,
    cfg: float,
) -> dict[str, Any]:
    return {
        "1": node("CheckpointLoaderSimple", {"ckpt_name": "ltx-video-2b-v0.9.5.safetensors"}),
        "2": node(
            "CLIPLoader",
            {
                "clip_name": "t5xxl_fp16.safetensors",
                "type": "ltxv",
                "device": "default",
            },
        ),
        "3": node("CLIPTextEncode", {"text": prompt, "clip": ["2", 0]}),
        "4": node("CLIPTextEncode", {"text": negative, "clip": ["2", 0]}),
        "5": node("LTXVConditioning", {"frame_rate": float(fps), "positive": ["3", 0], "negative": ["4", 0]}),
        "6": node(
            "EmptyLTXVLatentVideo",
            {
                "width": width,
                "height": height,
                "length": normalize_ltx_frames(length),
                "batch_size": 1,
            },
        ),
        "7": node(
            "LTXVScheduler",
            {
                "steps": steps,
                "max_shift": 2.05,
                "base_shift": 0.95,
                "stretch": True,
                "terminal": 0.1,
                "latent": ["6", 0],
            },
        ),
        "8": node("KSamplerSelect", {"sampler_name": "res_multistep"}),
        "9": node(
            "SamplerCustom",
            {
                "model": ["1", 0],
                "add_noise": True,
                "noise_seed": seed,
                "cfg": cfg,
                "positive": ["5", 0],
                "negative": ["5", 1],
                "sampler": ["8", 0],
                "sigmas": ["7", 0],
                "latent_image": ["6", 0],
            },
        ),
        "10": node("VAEDecode", {"samples": ["9", 0], "vae": ["1", 2]}),
        "11": node("CreateVideo", {"images": ["10", 0], "fps": float(fps)}),
        "12": node(
            "SaveVideo",
            {
                "video": ["11", 0],
                "filename_prefix": output_prefix("Mac_LTX_T2V"),
                "format": "mp4",
                "codec": "h264",
            },
        ),
    }


def build_ltx_i2v_prompt(
    *,
    prompt: str,
    negative: str,
    image_name: str,
    width: int,
    height: int,
    length: int,
    fps: int,
    seed: int,
    steps: int,
    cfg: float,
) -> dict[str, Any]:
    return {
        "1": node("LoadImage", {"image": image_name}),
        "2": node("LTXVPreprocess", {"image": ["1", 0], "img_compression": 40}),
        "3": node("CheckpointLoaderSimple", {"ckpt_name": "ltx-video-2b-v0.9.5.safetensors"}),
        "4": node(
            "CLIPLoader",
            {
                "clip_name": "t5xxl_fp16.safetensors",
                "type": "ltxv",
                "device": "default",
            },
        ),
        "5": node("CLIPTextEncode", {"text": prompt, "clip": ["4", 0]}),
        "6": node("CLIPTextEncode", {"text": negative, "clip": ["4", 0]}),
        "7": node(
            "LTXVImgToVideo",
            {
                "positive": ["5", 0],
                "negative": ["6", 0],
                "vae": ["3", 2],
                "image": ["2", 0],
                "width": width,
                "height": height,
                "length": normalize_ltx_frames(length),
                "batch_size": 1,
                "strength": 1.0,
            },
        ),
        "8": node("LTXVConditioning", {"frame_rate": float(fps), "positive": ["7", 0], "negative": ["7", 1]}),
        "9": node(
            "LTXVScheduler",
            {
                "steps": steps,
                "max_shift": 2.05,
                "base_shift": 0.95,
                "stretch": True,
                "terminal": 0.1,
                "latent": ["7", 2],
            },
        ),
        "10": node("KSamplerSelect", {"sampler_name": "euler"}),
        "11": node(
            "SamplerCustom",
            {
                "model": ["3", 0],
                "add_noise": True,
                "noise_seed": seed,
                "cfg": cfg,
                "positive": ["8", 0],
                "negative": ["8", 1],
                "sampler": ["10", 0],
                "sigmas": ["9", 0],
                "latent_image": ["7", 2],
            },
        ),
        "12": node("VAEDecode", {"samples": ["11", 0], "vae": ["3", 2]}),
        "13": node("CreateVideo", {"images": ["12", 0], "fps": float(fps)}),
        "14": node(
            "SaveVideo",
            {
                "video": ["13", 0],
                "filename_prefix": output_prefix("Mac_LTX_I2V"),
                "format": "mp4",
                "codec": "h264",
            },
        ),
    }


def build_a14b_i2v_prompt(
    *,
    prompt: str,
    negative: str,
    image_name: str,
    width: int,
    height: int,
    length: int,
    fps: int,
    seed: int,
    steps: int,
    cfg: float,
) -> dict[str, Any]:
    split_step = max(1, steps // 2)
    return {
        "10": node(
            "CLIPLoader",
            {
                "clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
                "type": "wan",
                "device": "default",
            },
        ),
        "11": node("CLIPTextEncode", {"text": prompt, "clip": ["10", 0]}),
        "12": node("CLIPTextEncode", {"text": negative, "clip": ["10", 0]}),
        "13": node("VAELoader", {"vae_name": "wan_2.1_vae.safetensors"}),
        "14": node("LoadImage", {"image": image_name}),
        "20": node(
            "WanImageToVideo",
            {
                "positive": ["11", 0],
                "negative": ["12", 0],
                "vae": ["13", 0],
                "width": width,
                "height": height,
                "length": length,
                "batch_size": 1,
                "start_image": ["14", 0],
            },
        ),
        "30": node(
            "UNETLoader",
            {
                "unet_name": "Wan2.2/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
                "weight_dtype": "default",
            },
        ),
        "31": node(
            "LoraLoaderModelOnly",
            {
                "model": ["30", 0],
                "lora_name": "Wan2.2/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
                "strength_model": 1.0,
            },
        ),
        "32": node("ModelSamplingSD3", {"model": ["31", 0], "shift": 5.0}),
        "40": node(
            "UNETLoader",
            {
                "unet_name": "Wan2.2/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
                "weight_dtype": "default",
            },
        ),
        "41": node(
            "LoraLoaderModelOnly",
            {
                "model": ["40", 0],
                "lora_name": "Wan2.2/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
                "strength_model": 1.0,
            },
        ),
        "42": node("ModelSamplingSD3", {"model": ["41", 0], "shift": 5.0}),
        "50": node(
            "KSamplerAdvanced",
            {
                "model": ["32", 0],
                "add_noise": "enable",
                "noise_seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "simple",
                "positive": ["20", 0],
                "negative": ["20", 1],
                "latent_image": ["20", 2],
                "start_at_step": 0,
                "end_at_step": split_step,
                "return_with_leftover_noise": "enable",
            },
        ),
        "51": node(
            "KSamplerAdvanced",
            {
                "model": ["42", 0],
                "add_noise": "disable",
                "noise_seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "simple",
                "positive": ["20", 0],
                "negative": ["20", 1],
                "latent_image": ["50", 0],
                "start_at_step": split_step,
                "end_at_step": steps,
                "return_with_leftover_noise": "disable",
            },
        ),
        "60": node(
            "VAEDecodeTiled",
            {
                "samples": ["51", 0],
                "vae": ["13", 0],
                "tile_size": 512,
                "overlap": 64,
                "temporal_size": 16,
                "temporal_overlap": 4,
            },
        ),
        "70": node("CreateVideo", {"images": ["60", 0], "fps": fps}),
        "80": node(
            "SaveVideo",
            {
                "video": ["70", 0],
                "filename_prefix": output_prefix("I2V_A14B"),
                "format": "mp4",
                "codec": "h264",
            },
        ),
    }


def build_a14b_t2v_prompt(
    *,
    prompt: str,
    negative: str,
    width: int,
    height: int,
    length: int,
    fps: int,
    seed: int,
    steps: int,
    cfg: float,
) -> dict[str, Any]:
    split_step = max(1, steps // 2)
    return {
        "10": node(
            "CLIPLoader",
            {
                "clip_name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
                "type": "wan",
                "device": "default",
            },
        ),
        "11": node("CLIPTextEncode", {"text": prompt, "clip": ["10", 0]}),
        "12": node("CLIPTextEncode", {"text": negative, "clip": ["10", 0]}),
        "13": node("VAELoader", {"vae_name": "wan_2.1_vae.safetensors"}),
        "20": node(
            "EmptyHunyuanLatentVideo",
            {"width": width, "height": height, "length": length, "batch_size": 1},
        ),
        "30": node(
            "UNETLoader",
            {
                "unet_name": "Wan2.2/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
                "weight_dtype": "default",
            },
        ),
        "31": node(
            "LoraLoaderModelOnly",
            {
                "model": ["30", 0],
                "lora_name": "Wan2.2/wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
                "strength_model": 1.0,
            },
        ),
        "32": node("ModelSamplingSD3", {"model": ["31", 0], "shift": 5.0}),
        "40": node(
            "UNETLoader",
            {
                "unet_name": "Wan2.2/wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
                "weight_dtype": "default",
            },
        ),
        "41": node(
            "LoraLoaderModelOnly",
            {
                "model": ["40", 0],
                "lora_name": "Wan2.2/wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors",
                "strength_model": 1.0,
            },
        ),
        "42": node("ModelSamplingSD3", {"model": ["41", 0], "shift": 5.0}),
        "50": node(
            "KSamplerAdvanced",
            {
                "model": ["32", 0],
                "add_noise": "enable",
                "noise_seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "simple",
                "positive": ["11", 0],
                "negative": ["12", 0],
                "latent_image": ["20", 0],
                "start_at_step": 0,
                "end_at_step": split_step,
                "return_with_leftover_noise": "enable",
            },
        ),
        "51": node(
            "KSamplerAdvanced",
            {
                "model": ["42", 0],
                "add_noise": "disable",
                "noise_seed": seed,
                "steps": steps,
                "cfg": cfg,
                "sampler_name": "euler",
                "scheduler": "simple",
                "positive": ["11", 0],
                "negative": ["12", 0],
                "latent_image": ["50", 0],
                "start_at_step": split_step,
                "end_at_step": steps,
                "return_with_leftover_noise": "disable",
            },
        ),
        "60": node(
            "VAEDecodeTiled",
            {
                "samples": ["51", 0],
                "vae": ["13", 0],
                "tile_size": 512,
                "overlap": 64,
                "temporal_size": 16,
                "temporal_overlap": 4,
            },
        ),
        "70": node("CreateVideo", {"images": ["60", 0], "fps": fps}),
        "80": node(
            "SaveVideo",
            {
                "video": ["70", 0],
                "filename_prefix": output_prefix("T2V_A14B"),
                "format": "mp4",
                "codec": "h264",
            },
        ),
    }


def build_rife_prompt(*, video_name: str, fps: int, multiplier: int) -> dict[str, Any]:
    multiplier = clamp_int(multiplier, 2, 4)
    output_fps = clamp_int(fps * multiplier, 1, 120)
    return {
        "1": node(
            "VHS_LoadVideo",
            {
                "video": video_name,
                "force_rate": fps,
                "force_size": "Disabled",
                "custom_width": 0,
                "custom_height": 0,
                "frame_load_cap": 0,
                "skip_first_frames": 0,
                "select_every_nth": 1,
            },
        ),
        "2": node(
            "RIFE VFI",
            {
                "ckpt_name": "rife49.pth",
                "frames": ["1", 0],
                "clear_cache_after_n_frames": 10,
                "multiplier": multiplier,
                "fast_mode": True,
                "ensemble": True,
                "scale_factor": 1.0,
                "dtype": "float16",
                "torch_compile": False,
                "batch_size": 4,
            },
        ),
        "3": node(
            "VHS_VideoCombine",
            {
                "images": ["2", 0],
                "frame_rate": output_fps,
                "loop_count": 0,
                "filename_prefix": output_prefix(f"RIFE_{multiplier}x"),
                "format": "video/h264-mp4",
                "pix_fmt": "yuv420p",
                "crf": 19,
                "save_metadata": True,
                "trim_to_audio": False,
                "pingpong": False,
                "save_output": True,
            },
        ),
    }


def build_video_upscale_prompt(*, video_name: str, fps: int, model_name: str = "RealESRGAN_x2plus.pth") -> dict[str, Any]:
    fps = clamp_int(fps, 1, 120)
    allowed_models = {"RealESRGAN_x2plus.pth", "4x-UltraSharp.pth"}
    if model_name not in allowed_models:
        model_name = "RealESRGAN_x2plus.pth"
    return {
        "1": node(
            "VHS_LoadVideo",
            {
                "video": video_name,
                "force_rate": fps,
                "force_size": "Disabled",
                "custom_width": 0,
                "custom_height": 0,
                "frame_load_cap": 0,
                "skip_first_frames": 0,
                "select_every_nth": 1,
            },
        ),
        "2": node("UpscaleModelLoader", {"model_name": model_name}),
        "3": node(
            "ImageUpscaleWithModel",
            {
                "upscale_model": ["2", 0],
                "image": ["1", 0],
            },
        ),
        "4": node(
            "VHS_VideoCombine",
            {
                "images": ["3", 0],
                "frame_rate": fps,
                "loop_count": 0,
                "filename_prefix": output_prefix("Upscale_2x"),
                "format": "video/h264-mp4",
                "pix_fmt": "yuv420p",
                "crf": 18,
                "save_metadata": True,
                "trim_to_audio": False,
                "pingpong": False,
                "save_output": True,
            },
        ),
    }


def build_preview_workflow_graph(
    *,
    mode: str,
    prompt: str,
    negative: str,
    width: int,
    height: int,
    length: int,
    fps: int,
    seed: int,
    steps: int,
    cfg: float,
    upscale_model: str,
    rife_multiplier: int,
) -> dict[str, Any] | None:
    if mode == "i2v_a14b":
        return build_a14b_i2v_prompt(
            prompt=prompt,
            negative=negative,
            image_name=DEFAULT_IMAGE,
            width=width,
            height=height,
            length=length,
            fps=fps,
            seed=seed,
            steps=steps,
            cfg=cfg,
        )
    if mode == "t2v_a14b":
        return build_a14b_t2v_prompt(
            prompt=prompt,
            negative=negative,
            width=width,
            height=height,
            length=length,
            fps=fps,
            seed=seed,
            steps=steps,
            cfg=cfg,
        )
    if mode == "ti2v_5b":
        return build_ti2v_prompt(
            prompt=prompt,
            negative=negative,
            image_name=DEFAULT_IMAGE,
            width=width,
            height=height,
            length=length,
            fps=fps,
            seed=seed,
            steps=steps,
            cfg=cfg,
        )
    if mode == "ltx_i2v":
        return build_ltx_i2v_prompt(
            prompt=prompt,
            negative=negative,
            image_name=DEFAULT_IMAGE,
            width=width,
            height=height,
            length=normalize_ltx_frames(length),
            fps=fps,
            seed=seed,
            steps=steps,
            cfg=cfg,
        )
    if mode == "ltx_t2v":
        return build_ltx_t2v_prompt(
            prompt=prompt,
            negative=negative,
            width=width,
            height=height,
            length=normalize_ltx_frames(length),
            fps=fps,
            seed=seed,
            steps=steps,
            cfg=cfg,
        )
    if mode == "rife_2x":
        return build_rife_prompt(video_name="sample.mp4", fps=fps, multiplier=rife_multiplier)
    if mode == "upscale_2x":
        return build_video_upscale_prompt(video_name="sample.mp4", fps=fps, model_name=upscale_model)
    return None


def validate_graph(prompt: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if not any(item.get("class_type") in {"SaveVideo", "VHS_VideoCombine", "SaveImage"} for item in prompt.values()):
        errors.append("缺少输出节点")
    for node_id, item in prompt.items():
        inputs = item.get("inputs", {})
        for key, value in inputs.items():
            if (
                isinstance(value, list)
                and len(value) == 2
                and isinstance(value[0], str)
                and isinstance(value[1], int)
                and value[0] not in prompt
            ):
                errors.append(f"{node_id}.{key} 连接到不存在的节点 {value[0]}")
    return errors


def graph_class_types(prompt: dict[str, Any]) -> list[str]:
    return sorted({str(item.get("class_type", "")) for item in prompt.values() if item.get("class_type")})


async def comfy_node_availability(prompt: dict[str, Any]) -> dict[str, Any]:
    class_types = graph_class_types(prompt)
    try:
        object_info = await comfy_get("/object_info")
    except HTTPException as exc:
        return {
            "connected": False,
            "missing": class_types,
            "error": str(exc.detail),
        }
    missing = [name for name in class_types if name not in object_info]
    return {
        "connected": True,
        "missing": missing,
        "error": "",
    }


def media_url(item: dict[str, Any]) -> str:
    filename = item.get("filename", "")
    subfolder = item.get("subfolder", "")
    file_type = item.get("type", "output")
    return (
        f"/api/view?filename={quote(filename)}"
        f"&subfolder={quote(subfolder)}"
        f"&type={quote(file_type)}"
    )


def extract_media(history_item: dict[str, Any]) -> list[dict[str, Any]]:
    media: list[dict[str, Any]] = []
    outputs = history_item.get("outputs", {})
    for node_output in outputs.values():
        for key in ("videos", "gifs", "images"):
            for item in node_output.get(key, []) or []:
                filename = item.get("filename")
                if not filename:
                    continue
                suffix = Path(filename).suffix.lower()
                kind = "video" if suffix in VIDEO_EXTENSIONS else "image"
                media.append(
                    {
                        "kind": kind,
                        "filename": filename,
                        "subfolder": item.get("subfolder", ""),
                        "type": item.get("type", "output"),
                        "url": media_url(item),
                    }
                )
    return media


def safe_media_path(filename: str, subfolder: str, file_type: str, paths: dict[str, Any] | None = None) -> Path:
    paths = paths or configured_comfy_paths()
    roots = {
        "input": Path(paths["input_dir"]),
        "output": Path(paths["output_dir"]),
        "temp": Path(paths["temp_dir"]),
    }
    root = roots.get(file_type)
    if root is None:
        raise HTTPException(status_code=400, detail="未知文件类型")

    base = root.resolve()
    candidate = (root / subfolder / filename).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=400, detail="文件路径不安全")
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="文件不存在")
    return candidate


def input_media_path(relative_name: str, paths: dict[str, Any] | None = None) -> Path:
    paths = paths or configured_comfy_paths()
    normalized = relative_name.replace("\\", "/")
    relative = Path(normalized)
    if relative.is_absolute() or ".." in relative.parts:
        raise HTTPException(status_code=400, detail="输入文件路径不安全")

    input_dir = Path(paths["input_dir"])
    base = input_dir.resolve()
    candidate = (input_dir / relative).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        raise HTTPException(status_code=400, detail="输入文件路径不安全")
    if not candidate.exists():
        raise HTTPException(status_code=404, detail="输入文件不存在")
    return candidate


def default_keyframe_errors(*, mode: str, has_upload: bool, paths: dict[str, Any] | None = None) -> list[str]:
    if mode not in IMAGE_WORKFLOW_MODES or has_upload:
        return []
    paths = paths or configured_comfy_paths()
    candidate = Path(paths["input_dir"]) / DEFAULT_IMAGE
    if candidate.exists():
        return []
    return [
        "内置示例关键帧缺失："
        f"{DEFAULT_IMAGE}。请回到“环境侦测”点击“一键安装/修复缺失项”，"
        "或在关键帧步骤上传自己的 PNG/JPG 图片。"
    ]


def output_media_item(path: Path, paths: dict[str, Any] | None = None) -> dict[str, Any]:
    paths = paths or configured_comfy_paths()
    output_base = Path(paths["output_dir"]).resolve()
    resolved = path.resolve()
    try:
        relative = resolved.relative_to(output_base)
    except ValueError as exc:
        raise HTTPException(status_code=500, detail="输出文件不在 ComfyUI output 目录") from exc

    subfolder = "" if relative.parent == Path(".") else str(relative.parent).replace("\\", "/")
    item = {
        "filename": relative.name,
        "subfolder": subfolder,
        "type": "output",
    }
    return {
        "kind": "video" if resolved.suffix.lower() in VIDEO_EXTENSIONS else "image",
        **item,
        "url": media_url(item),
    }


FFMPEG_INSTALL_HINT = (
    "请重新运行 START_WORKFLOW 启动器安装前端依赖，或手动安装 ffmpeg；"
    "Windows 可用 winget install Gyan.FFmpeg，macOS 可用 brew install ffmpeg。"
)


def ffmpeg_candidates() -> list[tuple[str, str]]:
    candidates: list[tuple[str, str]] = []
    env_path = os.environ.get("FFMPEG_PATH")
    if env_path:
        candidates.append(("FFMPEG_PATH", env_path.strip().strip('"')))
    system_path = shutil.which("ffmpeg")
    if system_path:
        candidates.append(("system", system_path))
    try:
        from imageio_ffmpeg import get_ffmpeg_exe

        candidates.append(("imageio-ffmpeg", get_ffmpeg_exe()))
    except Exception:
        pass
    unique: list[tuple[str, str]] = []
    seen: set[str] = set()
    for source, path in candidates:
        if path and path not in seen:
            unique.append((source, path))
            seen.add(path)
    return unique


def ffmpeg_probe() -> dict[str, Any]:
    errors: list[str] = []
    for source, path in ffmpeg_candidates():
        try:
            result = subprocess.run(
                [path, "-version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
        except Exception as exc:
            errors.append(f"{source}: {exc}")
            continue
        if result.returncode == 0:
            version = (result.stdout.splitlines() or ["ffmpeg"])[0]
            return {
                "name": "ffmpeg",
                "ok": True,
                "path": path,
                "source": source,
                "version": version,
                "message": f"已检测到 {source} ffmpeg。",
                "install_hint": "",
                "required": True,
            }
        errors.append(f"{source}: exit {result.returncode} {(result.stderr or result.stdout).strip()[:240]}")
    return {
        "name": "ffmpeg",
        "ok": False,
        "path": "",
        "source": "",
        "version": "",
        "message": "未找到可执行的 ffmpeg，闪烁修复不可用。" if not errors else "ffmpeg 不可执行：" + "；".join(errors[:3]),
        "install_hint": FFMPEG_INSTALL_HINT,
        "required": True,
    }


def find_ffmpeg() -> str:
    probe = ffmpeg_probe()
    if probe.get("ok") and probe.get("path"):
        return str(probe["path"])
    raise HTTPException(status_code=503, detail=probe.get("message") or "未找到 ffmpeg，无法执行闪烁修复")


def ffmpeg_filter_check(filter_name: str, probe: dict[str, Any] | None = None) -> dict[str, Any]:
    probe = probe or ffmpeg_probe()
    path = str(probe.get("path") or "")
    if not probe.get("ok") or not path:
        return {
            "name": f"ffmpeg:{filter_name}",
            "ok": False,
            "path": path,
            "source": probe.get("source", ""),
            "message": probe.get("message") or f"无法检测 {filter_name} 滤镜。",
            "install_hint": probe.get("install_hint") or FFMPEG_INSTALL_HINT,
            "required": True,
        }
    try:
        result = subprocess.run(
            [path, "-hide_banner", "-filters"],
            capture_output=True,
            check=True,
            text=True,
            timeout=20,
        )
        ok = filter_name in result.stdout
    except Exception as exc:
        return {
            "name": f"ffmpeg:{filter_name}",
            "ok": False,
            "path": path,
            "source": probe.get("source", ""),
            "message": f"检测 {filter_name} 滤镜失败：{exc}",
            "install_hint": FFMPEG_INSTALL_HINT,
            "required": True,
        }
    return {
        "name": f"ffmpeg:{filter_name}",
        "ok": ok,
        "path": path,
        "source": probe.get("source", ""),
        "message": f"已检测到 {filter_name} 滤镜。" if ok else f"当前 ffmpeg 缺少 {filter_name} 滤镜。",
        "install_hint": "" if ok else FFMPEG_INSTALL_HINT,
        "required": True,
    }


def ffmpeg_has_filter(filter_name: str) -> bool:
    return bool(ffmpeg_filter_check(filter_name).get("ok"))


def ffmpeg_tool_checks() -> list[dict[str, Any]]:
    probe = ffmpeg_probe()
    return [
        probe,
        ffmpeg_filter_check("deflicker", probe),
        ffmpeg_filter_check("hqdn3d", probe),
    ]


ASSET_METADATA = {
    "ti2v_5b": {"label": "Wan2.2 TI2V-5B fp16", "step": "试镜头"},
    "i2v_high": {"label": "Wan2.2 I2V-A14B high noise fp8", "step": "正式片段"},
    "i2v_low": {"label": "Wan2.2 I2V-A14B low noise fp8", "step": "正式片段"},
    "t2v_high": {"label": "Wan2.2 T2V-A14B high noise fp8", "step": "文字生视频"},
    "t2v_low": {"label": "Wan2.2 T2V-A14B low noise fp8", "step": "文字生视频"},
    "umt5": {"label": "UMT5 XXL fp8 文本编码器", "step": "视频生成"},
    "wan21_vae": {"label": "Wan 2.1 VAE", "step": "A14B 正片"},
    "wan22_vae": {"label": "Wan 2.2 VAE", "step": "TI2V 草稿"},
    "ltx_2b_095": {"label": "LTX-Video 2B 0.9.5", "step": "Mac LTX 视频"},
    "t5xxl_fp16": {"label": "T5 XXL fp16 文本编码器", "step": "Mac LTX 视频"},
    "i2v_lora_high": {"label": "I2V 4步 LoRA high noise", "step": "正式片段"},
    "i2v_lora_low": {"label": "I2V 4步 LoRA low noise", "step": "正式片段"},
    "t2v_lora_high": {"label": "T2V 4步 LoRA high noise", "step": "文字生视频"},
    "t2v_lora_low": {"label": "T2V 4步 LoRA low noise", "step": "文字生视频"},
    "rife49": {"label": "RIFE 4.9 插帧权重", "step": "RIFE 插帧"},
    "realesrgan_x2": {"label": "RealESRGAN x2 视频超分权重", "step": "清晰度增强"},
    "ultrasharp_x4": {"label": "4x-UltraSharp 备用超分权重", "step": "清晰度增强"},
}

CUSTOM_NODE_METADATA = {
    "video_helper_suite": {"label": "ComfyUI-VideoHelperSuite"},
    "frame_interpolation": {"label": "ComfyUI-Frame-Interpolation"},
}


def workflow_asset_manifest(base_dir: Path | None = None) -> list[dict[str, Any]]:
    base_dir = (base_dir or BASE_DIR).expanduser().resolve()
    input_dir = base_dir / "input"
    items: list[dict[str, Any]] = []
    for source in INSTALL_MODEL_FILES:
        item_id = str(source.get("id") or "")
        if not item_id:
            continue
        meta = ASSET_METADATA.get(item_id, {})
        items.append(
            {
                "id": item_id,
                "label": meta.get("label") or source.get("name") or item_id,
                "step": meta.get("step") or "工作流资产",
                "path": base_dir / str(source["dest"]),
                "bytes": int(source["bytes"]),
                "dest": str(source["dest"]),
                "name": str(source.get("name") or Path(str(source["dest"])).name),
                "url": effective_download_url(str(source.get("url") or "")),
                "installable": True,
            }
        )
    items.append(
        {
            "id": "sample_keyframe",
            "label": "内置示例关键帧",
            "step": "关键帧",
            "path": input_dir / DEFAULT_IMAGE,
            "bytes": None,
            "installable": True,
        }
    )
    return items


def custom_node_manifest(base_dir: Path | None = None) -> list[dict[str, Any]]:
    base_dir = (base_dir or BASE_DIR).expanduser().resolve()
    items: list[dict[str, Any]] = []
    for source in INSTALL_CUSTOM_NODE_REPOS:
        item_id = str(source.get("id") or "")
        if not item_id:
            continue
        meta = CUSTOM_NODE_METADATA.get(item_id, {})
        items.append(
            {
                "id": item_id,
                "label": meta.get("label") or source.get("name") or item_id,
                "path": base_dir / str(source["dest"]),
                "url": str(source.get("url") or ""),
                "installable": True,
            }
        )
    return items

def path_label(path: Path, base_dir: Path | None = None) -> str:
    base_dir = (base_dir or BASE_DIR).expanduser().resolve()
    try:
        return str(path.resolve().relative_to(base_dir)).replace("\\", "/")
    except ValueError:
        return str(path)


def collect_asset_checks(base_dir: Path | None = None) -> list[dict[str, Any]]:
    base_dir = (base_dir or BASE_DIR).expanduser().resolve()
    checks: list[dict[str, Any]] = []
    for item in workflow_asset_manifest(base_dir):
        path = item["path"]
        partial = path.with_name(path.name + ".part")
        exists = path.exists()
        size = path.stat().st_size if exists else 0
        partial_exists = partial.exists()
        partial_size = partial.stat().st_size if partial_exists else 0
        expected = item.get("bytes")
        size_ok = exists and (not expected or size == expected)
        local_candidate = None
        local_available = False
        if expected and not size_ok and INSTALL_FIND_LOCAL_ASSET_CANDIDATE:
            try:
                local_candidate = INSTALL_FIND_LOCAL_ASSET_CANDIDATE(
                    {
                        "id": item.get("id"),
                        "name": item.get("name") or path.name,
                        "dest": item.get("dest") or path.name,
                        "bytes": expected,
                    },
                    dest=path,
                    base_dir=base_dir,
                )
                local_available = bool(local_candidate)
            except Exception:
                local_candidate = None
                local_available = False
        remaining = None
        if expected:
            if size_ok:
                remaining = 0
            elif local_available:
                remaining = 0
            elif partial_exists:
                remaining = max(0, int(expected) - min(partial_size, int(expected)))
            elif exists:
                remaining = max(0, int(expected) - min(size, int(expected)))
            else:
                remaining = int(expected)
        if not exists and partial_exists and expected:
            if partial_size == int(expected):
                reason = f"已完整下载为 .part：{partial_size} / {expected} bytes；点击安装会自动转正"
            elif partial_size > int(expected):
                reason = f".part 大于预期：{partial_size} / {expected} bytes；请删除该 .part 后重试"
            else:
                reason = f"已下载 {partial_size} / {expected} bytes，可断点续传"
        elif local_available:
            reason = f"本地缓存可用：{local_candidate}；点击安装会复用本地文件"
        elif not exists:
            reason = "缺失"
        elif expected and size != expected:
            reason = f"大小不一致：{size} / {expected} bytes；重新安装会转为断点续传"
        else:
            reason = "已就绪"
        checks.append(
            {
                "id": item["id"],
                "label": item["label"],
                "step": item["step"],
                "path": str(path),
                "relative_path": path_label(path, base_dir),
                "ok": bool(size_ok),
                "exists": exists,
                "size_gb": round(size / 1024**3, 2) if exists else 0,
                "partial_exists": partial_exists,
                "partial_gb": round(partial_size / 1024**3, 2) if partial_exists else 0,
                "remaining_gb": round(remaining / 1024**3, 2) if remaining is not None else None,
                "expected_gb": round(expected / 1024**3, 2) if expected else None,
                "url": item.get("url") or "",
                "download_host": urlparse(str(item.get("url") or "")).hostname or "",
                "installable": bool(item.get("installable")),
                "local_available": local_available,
                "local_path": str(local_candidate) if local_candidate else "",
                "reason": reason,
            }
        )
    return checks


def collect_custom_node_checks(base_dir: Path | None = None) -> list[dict[str, Any]]:
    base_dir = (base_dir or BASE_DIR).expanduser().resolve()
    checks: list[dict[str, Any]] = []
    for item in custom_node_manifest(base_dir):
        path = item["path"]
        ok = path.exists() and path.is_dir()
        checks.append(
            {
                "id": item["id"],
                "label": item["label"],
                "path": str(path),
                "relative_path": path_label(path, base_dir),
                "url": item.get("url") or "",
                "download_host": urlparse(str(item.get("url") or "")).hostname or "",
                "ok": ok,
                "installable": bool(item.get("installable")),
                "reason": "已就绪" if ok else "缺失",
            }
        )
    return checks


def get_system_memory_gb() -> float | None:
    system_name = platform.system()
    try:
        if system_name == "Windows":
            import ctypes

            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            memory = MEMORYSTATUSEX()
            memory.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory))
            return round(memory.ullTotalPhys / 1024**3, 1)
        if system_name == "Darwin":
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True,
                check=True,
                text=True,
                timeout=5,
            )
            return round(int(result.stdout.strip()) / 1024**3, 1)
        meminfo = Path("/proc/meminfo")
        if meminfo.exists():
            for line in meminfo.read_text(encoding="utf-8", errors="ignore").splitlines():
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return round(kb / 1024**2, 1)
    except Exception:
        return None
    return None


def collect_torch_hardware() -> dict[str, Any]:
    info: dict[str, Any] = {
        "available": False,
        "version": None,
        "cuda_available": False,
        "mps_available": False,
        "devices": [],
    }
    try:
        import torch

        info["available"] = True
        info["version"] = getattr(torch, "__version__", None)
        info["cuda_available"] = bool(torch.cuda.is_available())
        if info["cuda_available"]:
            for index in range(torch.cuda.device_count()):
                props = torch.cuda.get_device_properties(index)
                info["devices"].append(
                    {
                        "index": index,
                        "name": torch.cuda.get_device_name(index),
                        "type": "cuda",
                        "vram_total_gb": round(props.total_memory / 1024**3, 1),
                    }
                )
        mps_backend = getattr(getattr(torch, "backends", None), "mps", None)
        if mps_backend is not None:
            info["mps_available"] = bool(mps_backend.is_available())
    except Exception as exc:
        info["error"] = str(exc)
    return info


def run_text_command(command: list[str], timeout: int = 8) -> str:
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace", check=True, timeout=timeout)
    return result.stdout.strip()


def mac_chip_tier(chip_name: str) -> str:
    value = chip_name.lower()
    if "ultra" in value:
        return "ultra"
    if "max" in value:
        return "max"
    if "pro" in value:
        return "pro"
    if value:
        return "base"
    return ""


def detect_mac_hardware() -> dict[str, Any]:
    info: dict[str, Any] = {
        "is_macos": platform.system() == "Darwin",
        "apple_silicon": False,
        "chip": "",
        "chip_tier": "",
        "unified_memory_gb": None,
        "machine_model": "",
        "machine_name": "",
        "mps_capable": False,
    }
    if not info["is_macos"]:
        return info

    machine = platform.machine()
    info["apple_silicon"] = machine == "arm64"
    try:
        info["chip"] = run_text_command(["sysctl", "-n", "machdep.cpu.brand_string"], timeout=5)
    except Exception:
        info["chip"] = platform.processor() or ""

    try:
        mem_bytes = int(run_text_command(["sysctl", "-n", "hw.memsize"], timeout=5))
        info["unified_memory_gb"] = round(mem_bytes / 1024**3, 1)
    except Exception:
        info["unified_memory_gb"] = get_system_memory_gb()

    try:
        raw = run_text_command(["system_profiler", "SPHardwareDataType", "-json"], timeout=12)
        payload = json.loads(raw)
        hardware_items = payload.get("SPHardwareDataType") or []
        if hardware_items:
            hardware = hardware_items[0]
            info["machine_model"] = hardware.get("machine_model", "") or ""
            info["machine_name"] = hardware.get("machine_name", "") or ""
            if hardware.get("chip_type"):
                info["chip"] = hardware.get("chip_type")
    except Exception:
        pass

    info["chip_tier"] = mac_chip_tier(str(info.get("chip") or ""))
    info["mps_capable"] = bool(info["apple_silicon"])
    return info


def probe_python_torch(python: Path) -> dict[str, Any]:
    probe: dict[str, Any] = {
        "python": str(python),
        "exists": python.exists(),
        "available": False,
        "cuda_available": False,
        "mps_built": False,
        "mps_available": False,
    }
    if not python.exists():
        return probe
    code = (
        "import json\n"
        "payload={'available': False, 'cuda_available': False, 'mps_built': False, 'mps_available': False}\n"
        "try:\n"
        " import torch\n"
        " payload['available']=True\n"
        " payload['version']=getattr(torch,'__version__',None)\n"
        " payload['cuda_available']=bool(torch.cuda.is_available())\n"
        " mps=getattr(getattr(torch,'backends',None),'mps',None)\n"
        " payload['mps_built']=bool(mps and mps.is_built())\n"
        " payload['mps_available']=bool(mps and mps.is_available())\n"
        "except Exception as exc:\n"
        " payload['error']=str(exc)\n"
        "print(json.dumps(payload))\n"
    )
    try:
        result = subprocess.run(
            [str(python), "-c", code],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
        if result.stdout.strip():
            probe.update(json.loads(result.stdout.strip().splitlines()[-1]))
        if result.returncode != 0 and result.stderr.strip():
            probe["error"] = result.stderr.strip()[-1000:]
    except Exception as exc:
        probe["error"] = str(exc)
    return probe


def comfy_torch_probe() -> dict[str, Any]:
    try:
        return probe_python_torch(comfy_venv_python())
    except Exception as exc:
        return {
            "python": str(COMFY_INSTALL_DIR),
            "exists": False,
            "available": False,
            "cuda_available": False,
            "mps_built": False,
            "mps_available": False,
            "error": str(exc),
        }


def comfy_devices_have_mps(devices: list[dict[str, Any]]) -> bool:
    for device in devices:
        text = f"{device.get('type', '')} {device.get('name', '')}".lower()
        if any(token in text for token in ("mps", "metal", "apple")):
            return True
    return False


def mac_video_tier(mac: dict[str, Any]) -> str:
    if not mac.get("is_macos"):
        return ""
    if not mac.get("apple_silicon"):
        return "mac_post_only"

    memory = float(mac.get("unified_memory_gb") or 0)
    chip_tier = str(mac.get("chip_tier") or "base")
    if memory < 12:
        tier = "mac_ltx_low"
    elif memory < 24:
        tier = "mac_ltx_low"
    elif memory < 32:
        tier = "mac_ltx_balanced"
    elif memory < 48:
        tier = "mac_ltx_quality"
    elif memory < 96:
        tier = "mac_wan5b_480p"
    else:
        tier = "mac_wan5b_720p_experimental"

    if chip_tier == "base" and tier not in {"mac_ltx_low", "mac_ltx_balanced"}:
        return "mac_ltx_balanced"
    if chip_tier == "pro" and tier in {"mac_wan5b_480p", "mac_wan5b_720p_experimental"}:
        return "mac_ltx_quality"
    return tier


def platform_strategy_for(
    *,
    mac: dict[str, Any],
    has_cuda: bool,
    has_mps_capability: bool,
) -> str:
    if mac.get("is_macos"):
        if mac.get("apple_silicon") and has_mps_capability:
            return "mac_mps"
        return "mac_post_only"
    if has_cuda:
        return "cuda_wan_workflow"
    return "post_only"


def summarize_comfy_devices(system_stats: dict[str, Any] | None) -> list[dict[str, Any]]:
    devices: list[dict[str, Any]] = []
    for index, device in enumerate((system_stats or {}).get("devices") or []):
        vram_total = device.get("vram_total") or device.get("total_memory") or 0
        vram_free = device.get("vram_free") or device.get("free_memory") or 0
        devices.append(
            {
                "index": index,
                "name": device.get("name") or f"Device {index}",
                "type": device.get("type") or device.get("device_type") or "",
                "vram_total_gb": round(vram_total / 1024**3, 1) if vram_total else None,
                "vram_free_gb": round(vram_free / 1024**3, 1) if vram_free else None,
            }
        )
    return devices


def detect_nvidia_smi_devices() -> list[dict[str, Any]]:
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return []
    try:
        result = subprocess.run(
            [
                nvidia_smi,
                "--query-gpu=name,memory.total,memory.free",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=True,
        )
    except Exception:
        return []
    devices: list[dict[str, Any]] = []
    for index, line in enumerate(result.stdout.splitlines()):
        parts = [part.strip() for part in line.split(",")]
        if len(parts) < 3:
            continue
        try:
            total_gb = round(float(parts[1]) / 1024, 1)
            free_gb = round(float(parts[2]) / 1024, 1)
        except ValueError:
            total_gb = 0
            free_gb = 0
        devices.append(
            {
                "index": index,
                "name": parts[0],
                "type": "cuda",
                "vram_total_gb": total_gb,
                "vram_free_gb": free_gb,
                "source": "nvidia-smi",
            }
        )
    return devices


def build_hardware_summary(system_stats: dict[str, Any] | None) -> dict[str, Any]:
    torch_info = collect_torch_hardware()
    mac_info = detect_mac_hardware()
    comfy_torch = comfy_torch_probe()
    comfy_devices = summarize_comfy_devices(system_stats)
    nvidia_smi_devices = detect_nvidia_smi_devices()
    devices = comfy_devices or torch_info.get("devices") or nvidia_smi_devices or []
    max_vram = max((device.get("vram_total_gb") or 0 for device in devices), default=0)
    sum_vram = round(sum((device.get("vram_total_gb") or 0 for device in devices)), 1)
    has_cuda = bool(torch_info.get("cuda_available")) or any(
        "cuda" in str(device.get("type", "")).lower() or "nvidia" in str(device.get("name", "")).lower()
        for device in devices
    )
    comfy_reports_mps = comfy_devices_have_mps(comfy_devices)
    front_mps_ready = bool(torch_info.get("mps_available"))
    comfy_mps_ready = bool(comfy_torch.get("mps_available")) or comfy_reports_mps
    has_mps_capability = bool(mac_info.get("mps_capable") or front_mps_ready or comfy_mps_ready)
    has_mps = bool(front_mps_ready or comfy_mps_ready or mac_info.get("mps_capable"))
    accelerator = "cuda" if has_cuda else "mps" if has_mps else "cpu"
    platform_strategy = platform_strategy_for(
        mac=mac_info,
        has_cuda=has_cuda,
        has_mps_capability=has_mps_capability,
    )
    mac_tier = mac_video_tier(mac_info)
    return {
        "accelerator": accelerator,
        "platform_strategy": platform_strategy,
        "devices": devices,
        "nvidia_smi_devices": nvidia_smi_devices,
        "gpu_count": len(devices),
        "max_vram_gb": round(max_vram, 1),
        "sum_vram_gb": sum_vram,
        "system_memory_gb": get_system_memory_gb(),
        "torch": torch_info,
        "front_torch_mps_ready": front_mps_ready,
        "comfy_torch": comfy_torch,
        "comfy_torch_mps_ready": comfy_mps_ready,
        "mac": mac_info,
        "mac_video_tier": mac_tier,
    }


def collect_node_checks(object_info: dict[str, Any] | None) -> list[dict[str, Any]]:
    required_nodes = [
        "SaveVideo",
        "CreateVideo",
        "LoadImage",
        "UNETLoader",
        "CLIPLoader",
        "VAELoader",
        "CLIPTextEncode",
        "WanImageToVideo",
        "Wan22ImageToVideoLatent",
        "EmptyHunyuanLatentVideo",
        "KSampler",
        "KSamplerAdvanced",
        "VAEDecodeTiled",
        "LoraLoaderModelOnly",
        "ModelSamplingSD3",
        "CheckpointLoaderSimple",
        "EmptyLTXVLatentVideo",
        "LTXVConditioning",
        "LTXVScheduler",
        "LTXVImgToVideo",
        "LTXVPreprocess",
        "SamplerCustom",
        "KSamplerSelect",
        "VAEDecode",
        "VHS_LoadVideo",
        "VHS_VideoCombine",
        "RIFE VFI",
        "UpscaleModelLoader",
        "ImageUpscaleWithModel",
    ]
    if object_info is None:
        return [{"name": name, "ok": False, "reason": "ComfyUI 未连接"} for name in required_nodes]
    return [{"name": name, "ok": name in object_info, "reason": "已加载" if name in object_info else "缺少"} for name in required_nodes]


def node_input_options(object_info: dict[str, Any] | None, node_name: str, input_name: str) -> list[str]:
    if not object_info or node_name not in object_info:
        return []
    node_info = object_info.get(node_name) or {}
    value = ((node_info.get("input") or {}).get("required") or {}).get(input_name)
    if value is None:
        value = ((node_info.get("input") or {}).get("optional") or {}).get(input_name)

    if isinstance(value, (list, tuple)):
        if value and isinstance(value[0], (list, tuple)):
            return [str(item) for item in value[0]]
        if len(value) > 1 and isinstance(value[1], dict) and isinstance(value[1].get("options"), (list, tuple)):
            return [str(item) for item in value[1]["options"]]
        if value and all(not isinstance(item, (dict, list, tuple)) for item in value):
            return [str(item) for item in value]
    if isinstance(value, dict) and isinstance(value.get("options"), (list, tuple)):
        return [str(item) for item in value["options"]]
    return []


def model_option_matches(options: list[str], expected: str) -> bool:
    expected_norm = expected.replace("\\", "/").lower()
    expected_base = Path(expected_norm).name
    for option in options:
        option_norm = option.replace("\\", "/").lower()
        if option_norm == expected_norm or option_norm.endswith(f"/{expected_norm}"):
            return True
        if Path(option_norm).name == expected_base:
            return True
    return False


def resolve_graph_model_option_names(graph: dict[str, Any], object_info: dict[str, Any]) -> dict[str, Any]:
    input_by_class = {
        "CheckpointLoaderSimple": "ckpt_name",
        "UNETLoader": "unet_name",
        "LoraLoaderModelOnly": "lora_name",
        "VAELoader": "vae_name",
        "CLIPLoader": "clip_name",
        "UpscaleModelLoader": "model_name",
    }
    for item in graph.values():
        class_type = item.get("class_type")
        input_name = input_by_class.get(str(class_type))
        if not input_name:
            continue
        inputs = item.get("inputs") or {}
        current = inputs.get(input_name)
        if not isinstance(current, str):
            continue
        options = node_input_options(object_info, str(class_type), input_name)
        for option in options:
            if model_option_matches([option], current):
                inputs[input_name] = option
                break
    return graph


def graph_model_registry_errors(graph: dict[str, Any], object_info: dict[str, Any] | None) -> list[str]:
    if object_info is None:
        return ["ComfyUI 未连接，无法验证 workflow 里的模型名。"]

    input_by_class = {
        "CheckpointLoaderSimple": "ckpt_name",
        "UNETLoader": "unet_name",
        "LoraLoaderModelOnly": "lora_name",
        "VAELoader": "vae_name",
        "CLIPLoader": "clip_name",
        "UpscaleModelLoader": "model_name",
    }
    errors: list[str] = []
    for node_id, item in graph.items():
        class_type = str(item.get("class_type") or "")
        input_name = input_by_class.get(class_type)
        if not input_name:
            continue
        inputs = item.get("inputs") or {}
        current = inputs.get(input_name)
        if not isinstance(current, str):
            continue
        options = node_input_options(object_info, class_type, input_name)
        if not model_option_matches(options, current):
            errors.append(f"{node_id}.{class_type}.{input_name} 模型未出现在 ComfyUI 列表：{current}")
    return errors


def workflow_risk_checks(*, mode: str, hardware: dict[str, Any], width: int, height: int, length: int) -> list[dict[str, str]]:
    checks: list[dict[str, str]] = []
    strategy = hardware.get("platform_strategy")
    if strategy != "mac_mps":
        return checks

    mps_ready = bool(hardware.get("front_torch_mps_ready") or hardware.get("comfy_torch_mps_ready"))
    if mode in {"i2v_a14b", "t2v_a14b"}:
        checks.append(
            {
                "level": "blocked",
                "title": "A14B Mac 专家风险",
                "message": "Wan2.2 A14B 的 fp8/MPS 兼容风险较高，Mac 默认不开放。请先使用 LTX 或 Wan5B 实验档。",
            }
        )
    if mode == "ti2v_5b":
        checks.append(
            {
                "level": "warn",
                "title": "Wan5B MPS smoke test",
                "message": "Mac 上运行 Wan5B 前建议先用 512x320、9 到 17 帧、低步数跑一次 smoke test，再提高到当前分辨率。",
            }
        )
        if width >= 1280 or height >= 704 or length > 49:
            checks.append(
                {
                    "level": "warn",
                    "title": "720P 高内存实验",
                    "message": "当前参数属于 Mac 高内存实验范围；如果出现 OOM、dtype 或黑屏，先退回 480P/短帧数。",
                }
            )
    if mode in {"ltx_i2v", "ltx_t2v"} and not mps_ready:
        checks.append(
            {
                "level": "warn",
                "title": "MPS 状态未确认",
                "message": "未在前端或 ComfyUI Python 中确认 torch.backends.mps 可用；这不一定代表不能跑，但建议先启动 ComfyUI 后刷新环境。",
            }
        )
    return checks


async def apply_comfy_model_option_names(graph: dict[str, Any]) -> dict[str, Any]:
    try:
        object_info = await comfy_get("/object_info")
    except HTTPException:
        return graph
    return resolve_graph_model_option_names(graph, object_info)


def collect_model_registry_checks(object_info: dict[str, Any] | None) -> list[dict[str, Any]]:
    expectations = [
        {
            "id": "ti2v_5b",
            "label": "Wan2.2 TI2V-5B fp16",
            "node": "UNETLoader",
            "input": "unet_name",
            "name": "Wan2.2/wan2.2_ti2v_5B_fp16.safetensors",
        },
        {
            "id": "i2v_high",
            "label": "Wan2.2 I2V-A14B high noise fp8",
            "node": "UNETLoader",
            "input": "unet_name",
            "name": "Wan2.2/wan2.2_i2v_high_noise_14B_fp8_scaled.safetensors",
        },
        {
            "id": "i2v_low",
            "label": "Wan2.2 I2V-A14B low noise fp8",
            "node": "UNETLoader",
            "input": "unet_name",
            "name": "Wan2.2/wan2.2_i2v_low_noise_14B_fp8_scaled.safetensors",
        },
        {
            "id": "t2v_high",
            "label": "Wan2.2 T2V-A14B high noise fp8",
            "node": "UNETLoader",
            "input": "unet_name",
            "name": "Wan2.2/wan2.2_t2v_high_noise_14B_fp8_scaled.safetensors",
        },
        {
            "id": "t2v_low",
            "label": "Wan2.2 T2V-A14B low noise fp8",
            "node": "UNETLoader",
            "input": "unet_name",
            "name": "Wan2.2/wan2.2_t2v_low_noise_14B_fp8_scaled.safetensors",
        },
        {
            "id": "umt5",
            "label": "UMT5 XXL fp8 文本编码器",
            "node": "CLIPLoader",
            "input": "clip_name",
            "name": "umt5_xxl_fp8_e4m3fn_scaled.safetensors",
        },
        {
            "id": "wan21_vae",
            "label": "Wan 2.1 VAE",
            "node": "VAELoader",
            "input": "vae_name",
            "name": "wan_2.1_vae.safetensors",
        },
        {
            "id": "wan22_vae",
            "label": "Wan 2.2 VAE",
            "node": "VAELoader",
            "input": "vae_name",
            "name": "wan2.2_vae.safetensors",
        },
        {
            "id": "ltx_2b_095",
            "label": "LTX-Video 2B 0.9.5",
            "node": "CheckpointLoaderSimple",
            "input": "ckpt_name",
            "name": "ltx-video-2b-v0.9.5.safetensors",
        },
        {
            "id": "t5xxl_fp16",
            "label": "T5 XXL fp16 文本编码器",
            "node": "CLIPLoader",
            "input": "clip_name",
            "name": "t5xxl_fp16.safetensors",
        },
        {
            "id": "i2v_lora_high",
            "label": "I2V 4步 LoRA high noise",
            "node": "LoraLoaderModelOnly",
            "input": "lora_name",
            "name": "Wan2.2/wan2.2_i2v_lightx2v_4steps_lora_v1_high_noise.safetensors",
        },
        {
            "id": "i2v_lora_low",
            "label": "I2V 4步 LoRA low noise",
            "node": "LoraLoaderModelOnly",
            "input": "lora_name",
            "name": "Wan2.2/wan2.2_i2v_lightx2v_4steps_lora_v1_low_noise.safetensors",
        },
        {
            "id": "t2v_lora_high",
            "label": "T2V 4步 LoRA high noise",
            "node": "LoraLoaderModelOnly",
            "input": "lora_name",
            "name": "Wan2.2/wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors",
        },
        {
            "id": "t2v_lora_low",
            "label": "T2V 4步 LoRA low noise",
            "node": "LoraLoaderModelOnly",
            "input": "lora_name",
            "name": "Wan2.2/wan2.2_t2v_lightx2v_4steps_lora_v1.1_low_noise.safetensors",
        },
        {
            "id": "realesrgan_x2",
            "label": "RealESRGAN x2 视频超分权重",
            "node": "UpscaleModelLoader",
            "input": "model_name",
            "name": "RealESRGAN_x2plus.pth",
        },
        {
            "id": "ultrasharp_x4",
            "label": "4x-UltraSharp 备用超分权重",
            "node": "UpscaleModelLoader",
            "input": "model_name",
            "name": "4x-UltraSharp.pth",
        },
    ]
    checks: list[dict[str, Any]] = []
    for item in expectations:
        options = node_input_options(object_info, item["node"], item["input"])
        ok = model_option_matches(options, item["name"])
        reason = "已出现在 ComfyUI 模型列表" if ok else "ComfyUI 模型列表未加载"
        if object_info is None:
            reason = "ComfyUI 未连接"
        elif item["node"] not in object_info:
            reason = f"节点未加载：{item['node']}"
        checks.append(
            {
                **item,
                "ok": ok,
                "reason": reason,
                "options_count": len(options),
            }
        )
    return checks


def build_load_diagnostics(
    *,
    paths: dict[str, Any],
    assets: list[dict[str, Any]],
    custom_nodes: list[dict[str, Any]],
    node_checks: list[dict[str, Any]],
    registry_checks: list[dict[str, Any]],
    object_info: dict[str, Any] | None,
) -> list[dict[str, str]]:
    diagnostics: list[dict[str, str]] = []
    if paths.get("base_dir_mismatch"):
        diagnostics.append(
            {
                "level": "warn",
                "title": "检测到 ComfyUI 实际目录和前端默认目录不同",
                "message": f"本次会按 ComfyUI 正在使用的目录工作：{paths['base_dir']}。如果之前下载到 {BASE_DIR}，那些文件不会被当前 ComfyUI 自动加载。",
            }
        )

    asset_by_id = {item["id"]: item for item in assets}
    loaded_missing = [
        item
        for item in registry_checks
        if not item["ok"] and asset_by_id.get(item["id"], {}).get("ok")
    ]
    if loaded_missing and object_info is not None:
        labels = "、".join(item["label"] for item in loaded_missing[:4])
        suffix = " 等" if len(loaded_missing) > 4 else ""
        diagnostics.append(
            {
                "level": "blocked",
                "title": "文件已存在，但 ComfyUI 模型列表没有加载",
                "message": f"{labels}{suffix} 已在磁盘上，但没有出现在 ComfyUI 的模型下拉列表里。通常是下载目录不一致，或安装后还没有重启 ComfyUI。",
            }
        )

    node_by_name = {item["name"]: item for item in node_checks}
    custom_by_id = {item["id"]: item for item in custom_nodes}
    if object_info is not None and custom_by_id.get("video_helper_suite", {}).get("ok") and not (
        node_by_name.get("VHS_LoadVideo", {}).get("ok") and node_by_name.get("VHS_VideoCombine", {}).get("ok")
    ):
        diagnostics.append(
            {
                "level": "blocked",
                "title": "VideoHelperSuite 目录存在，但节点未加载",
                "message": "这通常是 ComfyUI 没有重启，或自定义节点 requirements 没装进 ComfyUI 的 Python 环境。请重新执行一键安装缺失项，然后重启 ComfyUI。",
            }
        )
    if object_info is not None and custom_by_id.get("frame_interpolation", {}).get("ok") and not node_by_name.get("RIFE VFI", {}).get("ok"):
        diagnostics.append(
            {
                "level": "blocked",
                "title": "Frame-Interpolation 目录存在，但 RIFE 节点未加载",
                "message": "安装脚本现在会自动尝试安装该节点的 requirements。安装后需要重启 ComfyUI 才会出现 RIFE VFI 节点。",
            }
        )

    if object_info is None and any(item.get("ok") for item in custom_nodes):
        diagnostics.append(
            {
                "level": "warn",
                "title": "ComfyUI 尚未连接，节点加载状态待确认",
                "message": "磁盘上的自定义节点目录已检测到；请先启动 ComfyUI，前端会再从 /object_info 验证节点是否真的加载成功。",
            }
        )

    partial = [item for item in assets if item.get("exists") and not item.get("ok")]
    if partial:
        labels = "、".join(item["label"] for item in partial[:3])
        diagnostics.append(
            {
                "level": "warn",
                "title": "发现未完整下载的文件",
                "message": f"{labels} 文件大小不匹配。一键安装会尝试断点续传，若仍失败请删除对应 partial 文件后重试。",
            }
        )

    if not diagnostics:
        diagnostics.append(
            {
                "level": "ok",
                "title": "加载链路正常",
                "message": "模型文件、自定义节点目录和 ComfyUI 加载状态没有发现明显不一致。",
            }
        )
    return diagnostics


def model_recommendations(
    *,
    os_info: dict[str, Any],
    hardware: dict[str, Any],
    assets: list[dict[str, Any]],
    custom_nodes: list[dict[str, Any]],
    tool_checks: list[dict[str, Any]],
    node_checks: list[dict[str, Any]],
    base_dir: Path | None = None,
) -> list[dict[str, Any]]:
    base_dir = (base_dir or BASE_DIR).expanduser().resolve()
    asset_ok = {item["id"]: bool(item["ok"]) for item in assets}
    node_ok = {item["name"]: bool(item["ok"]) for item in node_checks}
    custom_ok = {item["id"]: bool(item["ok"]) for item in custom_nodes}
    tool_ok = {item["name"]: bool(item["ok"]) for item in tool_checks}
    accelerator = hardware.get("accelerator")
    max_vram = float(hardware.get("max_vram_gb") or 0)
    sum_vram = float(hardware.get("sum_vram_gb") or 0)
    gpu_count = int(hardware.get("gpu_count") or 0)
    is_macos = os_info["name"] == "Darwin"
    platform_strategy = hardware.get("platform_strategy")
    mac_tier = str(hardware.get("mac_video_tier") or "")
    mac_info = hardware.get("mac") or {}
    mac_memory = float(mac_info.get("unified_memory_gb") or hardware.get("system_memory_gb") or 0)
    mac_mps_ready = bool(hardware.get("front_torch_mps_ready") or hardware.get("comfy_torch_mps_ready"))
    post_only_video = platform_strategy in {"post_only", "mac_post_only"} or accelerator == "cpu"

    def all_assets(*ids: str) -> bool:
        return all(asset_ok.get(item) for item in ids)

    def all_nodes(*names: str) -> bool:
        return all(node_ok.get(item) for item in names)

    recommendations: list[dict[str, Any]] = []

    has_flux = (base_dir / "models" / "diffusion_models" / "flux2_dev_fp8mixed.safetensors").exists()
    has_sdxl = any((base_dir / "models" / "checkpoints").glob("*.safetensors"))
    keyframe_models = ["上传 PNG/JPG 关键帧"]
    if has_flux:
        keyframe_models.insert(0, "Flux/Flux2 本地生图")
    if has_sdxl:
        keyframe_models.insert(0, "SDXL 本地生图")
    recommendations.append(
        {
            "step": "关键帧",
            "status": "ok" if has_flux or has_sdxl else "warn",
            "recommended": keyframe_models,
            "reason": "检测到本地生图模型，可先生成首帧。" if has_flux or has_sdxl else "未检测到本地生图模型，也可以直接上传外部生成的关键帧。",
        }
    )

    ti2v_ready = all_assets("ti2v_5b", "umt5", "wan22_vae") and all_nodes(
        "Wan22ImageToVideoLatent", "UNETLoader", "CLIPLoader", "VAELoader"
    )
    ltx_ready = all_assets("ltx_2b_095", "t5xxl_fp16") and all_nodes(
        "CheckpointLoaderSimple",
        "CLIPLoader",
        "CLIPTextEncode",
        "LTXVConditioning",
        "LTXVScheduler",
        "LTXVImgToVideo",
        "SamplerCustom",
        "VAEDecode",
        "CreateVideo",
        "SaveVideo",
    )
    if platform_strategy == "mac_mps":
        if ltx_ready and mac_mps_ready:
            status_text = "ok"
            reason = f"检测到 Apple Silicon 与约 {mac_memory:g}GB 统一内存，默认用 LTX 短镜头跑通 Mac 视频生成。"
        elif ltx_ready:
            status_text = "warn"
            reason = "LTX 模型和节点已在，但当前没有确认 ComfyUI 的 MPS torch 状态；可先跑低分辨率 smoke test。"
        else:
            status_text = "warn"
            reason = "Mac 默认走 LTX 小模型路线；一键配置会先补齐 LTX 2B 与 T5 文本编码器。"
        recommended_ltx = ["Mac LTX I2V 低档/均衡档", "512x320 或 576x320", "2 到 3 秒短镜头"]
        if mac_tier in {"mac_ltx_quality", "mac_wan5b_480p", "mac_wan5b_720p_experimental"}:
            recommended_ltx = ["Mac LTX I2V 质量档", "704x416 或 768x512", "2 到 4 秒短镜头"]
        recommendations.append(
            {
                "step": "试镜头",
                "status": status_text,
                "recommended": recommended_ltx,
                "reason": reason,
            }
        )
    elif not ti2v_ready:
        status_text = "blocked"
        reason = "TI2V-5B 所需模型或 ComfyUI 节点不完整。"
    elif accelerator == "cuda" and max_vram >= 24:
        status_text = "ok"
        reason = f"单卡可用显存档位足够跑 720P 草稿，检测到最高单卡约 {max_vram:g}GB。"
    elif accelerator == "cuda" and max_vram >= 16:
        status_text = "warn"
        reason = "可尝试较短镜头或降低分辨率；建议开启 offload。"
    elif is_macos and accelerator == "mps":
        status_text = "warn"
        reason = "macOS 可作为兼容目标，但 Wan 视频节点在 MPS 上通常更慢且更挑版本。"
    else:
        status_text = "blocked"
        reason = "未检测到适合本地视频生成的 GPU 后端。"
    if platform_strategy != "mac_mps":
        draft_recommended = ["Wan2.2 TI2V-5B 720P", "横屏 1280x704 / 竖屏 704x1280", "3 到 5 秒短镜头"]
        if post_only_video:
            draft_recommended = ["当前硬件不建议本地生视频", "可上传外部视频进入后期", "或换 CUDA / Apple Silicon MPS 环境"]
        recommendations.append(
            {
                "step": "试镜头",
                "status": status_text,
                "recommended": draft_recommended,
                "reason": reason,
            }
        )

    i2v_ready = all_assets(
        "i2v_high",
        "i2v_low",
        "umt5",
        "wan21_vae",
        "i2v_lora_high",
        "i2v_lora_low",
    ) and all_nodes("WanImageToVideo", "KSamplerAdvanced", "LoraLoaderModelOnly")
    if platform_strategy == "mac_mps":
        if mac_tier == "mac_wan5b_720p_experimental":
            formal_status = "warn" if ti2v_ready else "blocked"
            formal_recommended = ["Wan2.2 TI2V-5B 720P 高内存实验档", "优先关键帧 I2V/TI2V", "先跑 smoke test"]
            formal_reason = "96GB/128GB+ Apple Silicon 可以尝试 Wan5B 720P 短镜头，但仍需通过 MPS dtype 与节点预检；A14B 不默认开放。"
        elif mac_tier == "mac_wan5b_480p":
            formal_status = "warn" if ti2v_ready else "blocked"
            formal_recommended = ["Wan2.2 TI2V-5B 480P 实验档", "短镜头", "必要时降低帧数"]
            formal_reason = "48GB/64GB Apple Silicon 可尝试 Wan5B 480P；720P 仍标为实验。"
        else:
            formal_status = "ok" if ltx_ready and mac_mps_ready else "warn"
            if mac_tier == "mac_ltx_low":
                formal_recommended = ["Mac LTX I2V 低档", "512x320", "1 到 2 秒短镜头"]
                formal_reason = "该 Mac 档位优先保证跑通，正式画质通过更短分镜、去闪烁和后期超分补足。"
            else:
                formal_recommended = ["Mac LTX I2V 质量档", "短镜头分镜", "后期超分"]
                formal_reason = "该 Mac 档位更适合 LTX 小模型短片段，正式画质通过分镜、去闪烁和超分补足。"
        recommendations.append(
            {
                "step": "正式片段",
                "status": formal_status,
                "recommended": formal_recommended,
                "reason": formal_reason,
            }
        )
    elif accelerator == "cuda" and max_vram >= 80:
        status_text = "ok" if i2v_ready else "blocked"
        reason = (
            f"适合跑 A14B 720P 正片，最高单卡约 {max_vram:g}GB。"
            if i2v_ready
            else "A14B I2V 所需模型、LoRA、VAE 或节点不完整。"
        )
        formal_recommended = ["Wan2.2 I2V-A14B fp8 + 4步 LoRA", "优先图生视频", "720P 3 到 5 秒"]
    elif accelerator == "cuda" and max_vram >= 48:
        status_text = "ok" if ti2v_ready else "blocked"
        reason = (
            "48GB 保守档默认用 Wan2.2 TI2V-5B 做正式片段；A14B 480P 只作为手动尝试。"
            if ti2v_ready
            else "Wan2.2 TI2V-5B 所需模型、VAE、文本编码器或节点不完整。"
        )
        formal_recommended = ["Wan2.2 TI2V-5B 720P 轻量正片", "A14B 480P 可手动尝试", "短镜头后期超分"]
    elif accelerator == "cuda" and max_vram >= 16:
        status_text = "ok" if ti2v_ready else "blocked"
        reason = (
            "16GB 到 24GB 更适合 Wan2.2 TI2V-5B 480P/短镜头；A14B 不作为默认路线。"
            if ti2v_ready
            else "Wan2.2 TI2V-5B 所需模型、VAE、文本编码器或节点不完整。"
        )
        formal_recommended = ["Wan2.2 TI2V-5B 480P 小显存正片", "短镜头", "必要时降低帧数"]
    elif not i2v_ready:
        status_text = "blocked"
        reason = "A14B I2V 所需模型、LoRA、VAE 或节点不完整。"
        formal_recommended = ["Wan2.2 I2V-A14B fp8 + 4步 LoRA", "优先图生视频", "720P 3 到 5 秒"]
    elif gpu_count >= 2 and sum_vram >= 80:
        status_text = "warn"
        reason = "多卡总显存足够，但单个 ComfyUI 视频任务通常更看单卡显存；可并行跑镜头，不等价于单卡 96GB。"
        formal_recommended = ["Wan2.2 I2V-A14B fp8 + 4步 LoRA", "优先图生视频", "720P 3 到 5 秒"]
    else:
        status_text = "blocked"
        reason = "A14B 正片建议至少 80GB 单卡显存；当前环境不建议直接跑 720P。"
        formal_recommended = ["Wan2.2 I2V-A14B fp8 + 4步 LoRA", "优先图生视频", "720P 3 到 5 秒"]
    if platform_strategy != "mac_mps":
        final_recommended = formal_recommended
        if post_only_video:
            final_recommended = ["当前硬件不建议本地生成正片", "先用外部视频或更强 GPU 生成", "本机可继续做去闪烁、插帧、超分"]
        recommendations.append(
            {
                "step": "正式片段",
                "status": status_text,
                "recommended": final_recommended,
                "reason": reason,
            }
        )

    t2v_ready = all_assets(
        "t2v_high",
        "t2v_low",
        "umt5",
        "wan21_vae",
        "t2v_lora_high",
        "t2v_lora_low",
    ) and all_nodes("EmptyHunyuanLatentVideo", "KSamplerAdvanced", "LoraLoaderModelOnly")
    if platform_strategy == "mac_mps":
        recommendations.append(
            {
                "step": "无图生视频",
                "status": "ok" if ltx_ready and mac_mps_ready else "warn",
                "recommended": ["Mac LTX T2V 低档/均衡档"],
                "reason": "Mac 上也可以文字生视频，但仍建议优先 I2V，人物和空间更可控。",
            }
        )
    else:
        recommendations.append(
            {
                "step": "无图生视频",
                "status": "ok" if t2v_ready and accelerator == "cuda" and max_vram >= 80 else "warn" if t2v_ready else "blocked",
                "recommended": ["Wan2.2 T2V-A14B fp8 + 4步 LoRA"],
                "reason": "可用，但品牌、人物和空间一致性不如 I2V；建议只在没有关键帧时使用。"
                if t2v_ready
                else "T2V-A14B 所需模型或节点不完整。",
            }
        )

    deflicker_ok = tool_ok.get("ffmpeg") and tool_ok.get("ffmpeg:deflicker") and tool_ok.get("ffmpeg:hqdn3d")
    recommendations.append(
        {
            "step": "闪烁修复",
            "status": "ok" if deflicker_ok else "blocked",
            "recommended": ["ffmpeg deflicker + hqdn3d"],
            "reason": "可修正曝光闪烁和轻微纹理跳动；人物结构变形仍要回到生成步骤重跑。"
            if deflicker_ok
            else "未检测到 ffmpeg 或所需滤镜。",
        }
    )

    rife_ready = asset_ok.get("rife49") and custom_ok.get("frame_interpolation") and all_nodes("RIFE VFI")
    recommendations.append(
        {
            "step": "插帧",
            "status": "ok" if rife_ready and accelerator == "cuda" else "warn" if rife_ready else "blocked",
            "recommended": ["RIFE 4.9 2x 插帧"],
            "reason": "CUDA 下适合把 24fps 提到 48fps。"
            if rife_ready and accelerator == "cuda"
            else "权重和节点已在，但非 CUDA 后端可能很慢或需要额外兼容配置。"
            if rife_ready
            else "RIFE 权重或自定义节点未就绪。",
        }
    )

    upscale_ready = asset_ok.get("realesrgan_x2") and all_nodes("UpscaleModelLoader", "ImageUpscaleWithModel")
    recommendations.append(
        {
            "step": "清晰度增强",
            "status": "ok" if upscale_ready and accelerator == "cuda" else "warn" if upscale_ready else "blocked",
            "recommended": ["RealESRGAN_x2plus 2x", "4x-UltraSharp 备用"],
            "reason": "默认把 1280x704 提到 2560x1408，再交给剪辑软件导出 1080P/4K。"
            if upscale_ready
            else "超分权重或 ComfyUI 超分节点未就绪。",
        }
    )

    recommendations.append(
        {
            "step": "拼接剪辑",
            "status": "ok",
            "recommended": ["剪映", "DaVinci Resolve", "Premiere Pro"],
            "reason": "模型负责短镜头，剪辑软件负责节奏、调色、字幕和声音。",
        }
    )
    return recommendations


def workflow_model_options(
    *,
    os_info: dict[str, Any],
    hardware: dict[str, Any],
    assets: list[dict[str, Any]],
    custom_nodes: list[dict[str, Any]],
    tool_checks: list[dict[str, Any]],
    node_checks: list[dict[str, Any]],
    base_dir: Path | None = None,
) -> dict[str, Any]:
    base_dir = (base_dir or BASE_DIR).expanduser().resolve()
    asset_ok = {item["id"]: bool(item["ok"]) for item in assets}
    node_ok = {item["name"]: bool(item["ok"]) for item in node_checks}
    custom_ok = {item["id"]: bool(item["ok"]) for item in custom_nodes}
    tool_ok = {item["name"]: bool(item["ok"]) for item in tool_checks}
    accelerator = hardware.get("accelerator")
    max_vram = float(hardware.get("max_vram_gb") or 0)
    is_macos = os_info["name"] == "Darwin"
    platform_strategy = hardware.get("platform_strategy")
    mac_tier = str(hardware.get("mac_video_tier") or "")
    mac_info = hardware.get("mac") or {}
    mac_memory = float(mac_info.get("unified_memory_gb") or hardware.get("system_memory_gb") or 0)
    mac_mps_ready = bool(hardware.get("front_torch_mps_ready") or hardware.get("comfy_torch_mps_ready"))
    post_only_video = platform_strategy in {"post_only", "mac_post_only"} or accelerator == "cpu"

    def all_assets(*ids: str) -> bool:
        return all(asset_ok.get(item) for item in ids)

    def all_nodes(*names: str) -> bool:
        return all(node_ok.get(item) for item in names)

    def cuda_status(*, ready: bool, ok_gb: float, warn_gb: float, mac_warn: bool = False) -> str:
        if not ready:
            return "blocked"
        if accelerator == "cuda" and max_vram >= ok_gb:
            return "ok"
        if accelerator == "cuda" and max_vram >= warn_gb:
            return "warn"
        if is_macos and accelerator == "mps" and mac_warn:
            return "warn"
        return "blocked"

    def mac_ltx_status(ready: bool) -> str:
        if platform_strategy != "mac_mps":
            return "blocked"
        if not ready:
            return "blocked"
        if ready and mac_mps_ready:
            return "ok"
        return "warn"

    def mac_wan5b_status(ready: bool, *, min_memory_gb: float) -> str:
        if platform_strategy != "mac_mps" or mac_memory < min_memory_gb:
            return "blocked"
        if not ready:
            return "blocked"
        if ready and mac_mps_ready:
            return "warn"
        return "warn"

    def option(
        *,
        id: str,
        label: str,
        step: str,
        mode: str,
        status: str,
        reason: str,
        defaults: dict[str, Any] | None = None,
        uses_image: bool = True,
        model_label: str | None = None,
        supported: bool = True,
        scale: int | None = None,
        upscale_model: str | None = None,
        rife_multiplier: int | None = None,
    ) -> dict[str, Any]:
        return {
            "id": id,
            "label": label,
            "step": step,
            "mode": mode,
            "status": status,
            "reason": reason,
            "defaults": defaults or {},
            "uses_image": uses_image,
            "model_label": model_label or label,
            "supported": supported,
            "scale": scale,
            "upscale_model": upscale_model,
            "rife_multiplier": rife_multiplier,
        }

    ti2v_ready = all_assets("ti2v_5b", "umt5", "wan22_vae") and all_nodes(
        "Wan22ImageToVideoLatent", "UNETLoader", "CLIPLoader", "VAELoader", "KSampler"
    )
    i2v_ready = all_assets(
        "i2v_high",
        "i2v_low",
        "umt5",
        "wan21_vae",
        "i2v_lora_high",
        "i2v_lora_low",
    ) and all_nodes("WanImageToVideo", "KSamplerAdvanced", "LoraLoaderModelOnly")
    t2v_ready = all_assets(
        "t2v_high",
        "t2v_low",
        "umt5",
        "wan21_vae",
        "t2v_lora_high",
        "t2v_lora_low",
    ) and all_nodes("EmptyHunyuanLatentVideo", "KSamplerAdvanced", "LoraLoaderModelOnly")
    ltx_ready = all_assets("ltx_2b_095", "t5xxl_fp16") and all_nodes(
        "CheckpointLoaderSimple",
        "CLIPLoader",
        "CLIPTextEncode",
        "LTXVConditioning",
        "LTXVScheduler",
        "LTXVImgToVideo",
        "SamplerCustom",
        "VAEDecode",
        "CreateVideo",
        "SaveVideo",
    )
    deflicker_ready = bool(tool_ok.get("ffmpeg") and tool_ok.get("ffmpeg:deflicker") and tool_ok.get("ffmpeg:hqdn3d"))
    rife_ready = bool(asset_ok.get("rife49") and custom_ok.get("frame_interpolation") and all_nodes("RIFE VFI"))
    upscale_ready = all_nodes("UpscaleModelLoader", "ImageUpscaleWithModel")
    has_flux = (base_dir / "models" / "diffusion_models" / "flux2_dev_fp8mixed.safetensors").exists()
    has_sdxl = any((base_dir / "models" / "checkpoints").glob("*.safetensors"))

    options = {
        "keyframe": [
            option(
                id="upload_keyframe",
                label="上传/外部关键帧",
                step="keyframe",
                mode="",
                status="ok",
                reason="最稳妥，任何硬件都可用；先把构图定死再做视频。",
                uses_image=True,
            ),
            option(
                id="sdxl_keyframe",
                label="SDXL 本地生图",
                step="keyframe",
                mode="",
                status="ok" if has_sdxl else "warn",
                reason="适合 8GB 以上 CUDA 或 Apple Silicon 生成关键帧；未接入自动生图按钮时可在 ComfyUI 里生成后上传。",
                uses_image=True,
                supported=False,
            ),
            option(
                id="flux_keyframe",
                label="Flux/Flux2 本地生图",
                step="keyframe",
                mode="",
                status="ok" if has_flux and (max_vram >= 24 or is_macos) else "warn",
                reason="画质和商业摄影感更强；低显存或 macOS 需要更谨慎的量化/低显存工作流。",
                uses_image=True,
                supported=False,
            ),
        ],
        "draft": [
            option(
                id="no_local_video_draft",
                label="当前硬件不建议本地生视频",
                step="draft",
                mode="unsupported",
                status="blocked",
                reason="未检测到 CUDA 或可用 Apple Silicon MPS。可以继续用本机做后期，或换更合适的硬件生成视频。",
                uses_image=False,
                supported=False,
            ),
            option(
                id="mac_ltx_low_i2v",
                label="Mac LTX I2V 低档",
                step="draft",
                mode="ltx_i2v",
                status=mac_ltx_status(ltx_ready),
                reason="Apple Silicon 低内存或首次 smoke test 档；小分辨率、短镜头，优先验证能跑通。",
                defaults={"width": 512, "height": 320, "length": 25, "fps": 24, "steps": 12, "cfg": 3.0},
            ),
            option(
                id="mac_ltx_balanced_i2v",
                label="Mac LTX I2V 均衡档",
                step="draft",
                mode="ltx_i2v",
                status=mac_ltx_status(ltx_ready),
                reason="16GB 到 32GB+ Apple Silicon 的默认 Mac 视频草稿路线，速度和稳定性优先。",
                defaults={"width": 576, "height": 320, "length": 49, "fps": 24, "steps": 16, "cfg": 3.0},
            ),
            option(
                id="mac_ltx_quality_i2v",
                label="Mac LTX I2V 质量档",
                step="draft",
                mode="ltx_i2v",
                status=mac_ltx_status(ltx_ready),
                reason="高内存 Apple Silicon 可先用 LTX 质量档试动作和镜头，再决定是否切 Wan5B。",
                defaults={"width": 704, "height": 416, "length": 49, "fps": 24, "steps": 18, "cfg": 3.0},
            ),
            option(
                id="wan22_ti2v_5b_720p",
                label="Wan2.2 TI2V-5B 720P",
                step="draft",
                mode="ti2v_5b",
                status=cuda_status(ready=ti2v_ready, ok_gb=24, warn_gb=16, mac_warn=True),
                reason="推荐草稿模型。24GB 以上 CUDA 更舒服，macOS 可实验但速度和节点兼容性要看环境。",
                defaults={"width": 1280, "height": 704, "length": 81, "fps": 24, "steps": 20, "cfg": 5.0},
            ),
            option(
                id="wan22_ti2v_5b_480p",
                label="Wan2.2 TI2V-5B 480P 小显存",
                step="draft",
                mode="ti2v_5b",
                status=cuda_status(ready=ti2v_ready, ok_gb=16, warn_gb=8, mac_warn=True),
                reason="给 8GB 到 16GB、Apple Silicon 或不稳定机器的保守档；更慢但更容易跑通。",
                defaults={"width": 832, "height": 480, "length": 49, "fps": 24, "steps": 18, "cfg": 5.0},
            ),
        ],
        "final": [
            option(
                id="no_local_video_final",
                label="当前硬件不建议本地生成正片",
                step="final",
                mode="unsupported",
                status="blocked",
                reason="当前为后期处理路线，不会下载视频大模型。请上传外部视频进入后期，或切换到 CUDA / Apple Silicon MPS 环境。",
                uses_image=False,
                supported=False,
            ),
            option(
                id="mac_ltx_low_i2v",
                label="Mac LTX I2V 低档",
                step="final",
                mode="ltx_i2v",
                status=mac_ltx_status(ltx_ready),
                reason="8GB/16GB Apple Silicon 的保守正式片段档。短镜头、小分辨率，优先保证跑通。",
                defaults={"width": 512, "height": 320, "length": 25, "fps": 24, "steps": 12, "cfg": 3.0},
            ),
            option(
                id="mac_ltx_quality_i2v",
                label="Mac LTX I2V 质量档",
                step="final",
                mode="ltx_i2v",
                status=mac_ltx_status(ltx_ready),
                reason="Mac 默认正式片段路线。先用关键帧稳定构图，再靠短镜头、去闪烁和超分补质量。",
                defaults={"width": 704, "height": 416, "length": 49, "fps": 24, "steps": 18, "cfg": 3.0},
            ),
            option(
                id="mac_ltx_balanced_t2v",
                label="Mac LTX T2V 均衡档",
                step="final",
                mode="ltx_t2v",
                status=mac_ltx_status(ltx_ready),
                reason="没有关键帧时可用；随机性更强，仍建议先做关键帧再 I2V。",
                defaults={"width": 576, "height": 320, "length": 49, "fps": 24, "steps": 16, "cfg": 3.0},
                uses_image=False,
            ),
            option(
                id="mac_wan5b_480p",
                label="Mac Wan2.2 TI2V-5B 480P 实验档",
                step="final",
                mode="ti2v_5b",
                status=mac_wan5b_status(ti2v_ready, min_memory_gb=24),
                reason="高内存 Apple Silicon 的质量尝试档。需要先通过节点、模型名和 MPS dtype 预检。",
                defaults={"width": 832, "height": 480, "length": 49, "fps": 24, "steps": 20, "cfg": 5.0},
            ),
            option(
                id="mac_wan5b_720p_experimental",
                label="Mac Wan2.2 TI2V-5B 720P 高内存实验档",
                step="final",
                mode="ti2v_5b",
                status=mac_wan5b_status(ti2v_ready, min_memory_gb=96),
                reason="96GB/128GB+ Apple Silicon 可尝试的短镜头档；A14B 仍不作为 Mac 默认推荐。",
                defaults={"width": 1280, "height": 704, "length": 49, "fps": 24, "steps": 20, "cfg": 5.0},
            ),
            option(
                id="wan22_i2v_a14b_720p",
                label="Wan2.2 I2V-A14B 720P",
                step="final",
                mode="i2v_a14b",
                status=cuda_status(ready=i2v_ready, ok_gb=80, warn_gb=64),
                reason="96GB/80GB 单卡首选，用关键帧控制画面，适合正式出片。",
                defaults={"width": 1280, "height": 704, "length": 81, "fps": 24, "steps": 4, "cfg": 1.0},
            ),
            option(
                id="wan22_i2v_a14b_480p",
                label="Wan2.2 I2V-A14B 480P",
                step="final",
                mode="i2v_a14b",
                status=cuda_status(ready=i2v_ready, ok_gb=48, warn_gb=32),
                reason="48GB 单卡更现实的 A14B 档位；可先 480P 出片，再做超分。",
                defaults={"width": 832, "height": 480, "length": 49, "fps": 24, "steps": 4, "cfg": 1.0},
            ),
            option(
                id="wan22_t2v_a14b_720p",
                label="Wan2.2 T2V-A14B 720P",
                step="final",
                mode="t2v_a14b",
                status=cuda_status(ready=t2v_ready, ok_gb=80, warn_gb=64),
                reason="没有关键帧时使用；随机性更大，品牌和人物一致性不如 I2V。",
                defaults={"width": 1280, "height": 704, "length": 81, "fps": 24, "steps": 4, "cfg": 1.0},
                uses_image=False,
            ),
            option(
                id="wan22_t2v_a14b_480p",
                label="Wan2.2 T2V-A14B 480P",
                step="final",
                mode="t2v_a14b",
                status=cuda_status(ready=t2v_ready, ok_gb=48, warn_gb=32),
                reason="48GB 或更紧张显存的文字生视频尝试档；仍建议优先 I2V。",
                defaults={"width": 832, "height": 480, "length": 49, "fps": 24, "steps": 4, "cfg": 1.0},
                uses_image=False,
            ),
            option(
                id="wan22_ti2v_5b_final_720p",
                label="Wan2.2 TI2V-5B 720P 轻量正片",
                step="final",
                mode="ti2v_5b",
                status=cuda_status(ready=ti2v_ready, ok_gb=24, warn_gb=16, mac_warn=True),
                reason="24GB 到 48GB 可以把 5B 当最终模型用，画质不如 A14B，但更容易跑通。",
                defaults={"width": 1280, "height": 704, "length": 81, "fps": 24, "steps": 24, "cfg": 5.0},
            ),
            option(
                id="wan22_ti2v_5b_final_480p",
                label="Wan2.2 TI2V-5B 480P 小显存正片",
                step="final",
                mode="ti2v_5b",
                status=cuda_status(ready=ti2v_ready, ok_gb=16, warn_gb=8, mac_warn=True),
                reason="低配 CUDA、Apple Silicon 或测试机的保底视频生成档。",
                defaults={"width": 832, "height": 480, "length": 49, "fps": 24, "steps": 22, "cfg": 5.0},
            ),
        ],
        "deflicker": [
            option(
                id="ffmpeg_deflicker_balanced",
                label="ffmpeg deflicker + hqdn3d",
                step="deflicker",
                mode="deflicker",
                status="ok" if deflicker_ready else "blocked",
                reason="CPU 也能跑，适合修曝光闪烁和轻微纹理跳动。",
                uses_image=False,
            )
        ],
        "rife": [
            option(
                id="rife49_2x",
                label="RIFE 4.9 2x",
                step="rife",
                mode="rife_2x",
                status="ok" if rife_ready and accelerator == "cuda" else "warn" if rife_ready else "blocked",
                reason="把 24fps 插到 48fps；CUDA 最稳，macOS/CPU 可能很慢。",
                uses_image=False,
                rife_multiplier=2,
            ),
            option(
                id="rife49_4x",
                label="RIFE 4.9 4x",
                step="rife",
                mode="rife_2x",
                status="ok" if rife_ready and accelerator == "cuda" and max_vram >= 16 else "warn" if rife_ready else "blocked",
                reason="更高帧率，耗时和伪影风险更高；只建议短片段。",
                uses_image=False,
                rife_multiplier=4,
            ),
        ],
        "upscale": [
            option(
                id="realesrgan_x2plus",
                label="RealESRGAN x2plus 2x",
                step="upscale",
                mode="upscale_2x",
                status="ok" if upscale_ready and asset_ok.get("realesrgan_x2") else "blocked",
                reason="默认超分，稳妥地把 720P 提到约 2x。",
                uses_image=False,
                scale=2,
                upscale_model="RealESRGAN_x2plus.pth",
            ),
            option(
                id="ultrasharp_4x",
                label="4x-UltraSharp 4x",
                step="upscale",
                mode="upscale_2x",
                status="ok" if upscale_ready and asset_ok.get("ultrasharp_x4") and max_vram >= 16 else "warn" if asset_ok.get("ultrasharp_x4") else "blocked",
                reason="目标分辨率更高，文件和显存压力明显增加；适合最终短片段。",
                uses_image=False,
                scale=4,
                upscale_model="4x-UltraSharp.pth",
            ),
        ],
    }

    def first_ok(step: str, fallback: str) -> str:
        for item in options[step]:
            if item["status"] == "ok" and item["supported"]:
                return item["id"]
        for item in options[step]:
            if item["status"] == "warn" and item["supported"]:
                return item["id"]
        return fallback

    if platform_strategy == "mac_mps":
        if mac_tier == "mac_wan5b_720p_experimental":
            final_default = "mac_wan5b_720p_experimental"
            draft_default = "mac_ltx_quality_i2v"
        elif mac_tier == "mac_wan5b_480p":
            final_default = "mac_wan5b_480p"
            draft_default = "mac_ltx_quality_i2v"
        elif mac_tier == "mac_ltx_quality":
            final_default = "mac_ltx_quality_i2v"
            draft_default = "mac_ltx_balanced_i2v"
        elif mac_tier == "mac_ltx_balanced":
            final_default = "mac_ltx_quality_i2v"
            draft_default = "mac_ltx_balanced_i2v"
        else:
            final_default = "mac_ltx_low_i2v"
            draft_default = "mac_ltx_low_i2v"
    elif accelerator == "cuda" and max_vram >= 80:
        final_default = "wan22_i2v_a14b_720p"
        draft_default = "wan22_ti2v_5b_720p"
    elif accelerator == "cuda" and max_vram >= 48:
        final_default = "wan22_ti2v_5b_final_720p"
        draft_default = "wan22_ti2v_5b_720p"
    elif accelerator == "cuda" and max_vram >= 16:
        final_default = "wan22_ti2v_5b_final_480p"
        draft_default = "wan22_ti2v_5b_480p"
    elif post_only_video:
        final_default = "no_local_video_final"
        draft_default = "no_local_video_draft"
    else:
        final_default = first_ok("final", "wan22_ti2v_5b_final_480p")
        draft_default = first_ok("draft", "wan22_ti2v_5b_480p")

    recommended = {
        "keyframe": "flux_keyframe" if has_flux else "sdxl_keyframe" if has_sdxl else "upload_keyframe",
        "draft": draft_default,
        "final": final_default,
        "deflicker": first_ok("deflicker", "ffmpeg_deflicker_balanced"),
        "rife": first_ok("rife", "rife49_2x"),
        "upscale": "realesrgan_x2plus",
    }

    small_model_routes = [
        {
            "hardware": "8GB-12GB CUDA / 入门显卡",
            "route": "关键帧 + Wan2.2 TI2V-5B 480P 短镜头，或另接 LTX-Video / AnimateDiff 小模型工作流。",
        },
        {
            "hardware": "16GB-24GB CUDA",
            "route": "Wan2.2 TI2V-5B 480P/720P；A14B 不建议作为主力。",
        },
        {
            "hardware": "48GB CUDA",
            "route": "TI2V-5B 720P 稳定可用；A14B 建议 480P、短镜头、必要时 offload。",
        },
        {
            "hardware": "80GB-96GB CUDA",
            "route": "Wan2.2 I2V-A14B 720P 主线；TI2V-5B 做草稿。",
        },
        {
            "hardware": "Apple Silicon / macOS",
            "route": "8GB/16GB 默认 Mac LTX 低档；24GB-36GB 用 LTX 均衡/质量档；48GB+ 可试 Wan2.2 TI2V-5B 480P；96GB/128GB+ 可试 720P 实验档。",
        },
        {
            "hardware": "CPU / 无独显",
            "route": "不建议本地生视频；适合关键帧管理、剪辑拼接、ffmpeg 闪烁修复。",
        },
    ]

    return {
        "options": options,
        "recommended": recommended,
        "small_model_routes": small_model_routes,
    }


VIDEO_SMOKE_PRIORITY = [
    "mac_ltx_low_i2v",
    "mac_ltx_balanced_i2v",
    "mac_ltx_quality_i2v",
    "wan22_ti2v_5b_480p",
    "wan22_ti2v_5b_720p",
    "mac_wan5b_480p",
    "wan22_ti2v_5b_final_480p",
    "wan22_ti2v_5b_final_720p",
    "mac_wan5b_720p_experimental",
    "wan22_i2v_a14b_480p",
    "wan22_i2v_a14b_720p",
]


def choose_video_smoke_option(model_options: dict[str, Any]) -> dict[str, Any] | None:
    candidates: dict[str, dict[str, Any]] = {}
    for step_id in ("draft", "final"):
        for item in (model_options.get("options") or {}).get(step_id, []):
            if item.get("mode") in IMAGE_WORKFLOW_MODES and item.get("supported") and item.get("status") in {"ok", "warn"}:
                candidates[str(item.get("id"))] = item
    for item_id in VIDEO_SMOKE_PRIORITY:
        if item_id in candidates:
            return candidates[item_id]
    return next(iter(candidates.values()), None)


def video_smoke_parameters(option: dict[str, Any]) -> dict[str, Any]:
    mode = str(option.get("mode") or "")
    defaults = dict(option.get("defaults") or {})
    if mode == "ltx_i2v":
        return {
            "width": min(int(defaults.get("width") or 512), 512),
            "height": min(int(defaults.get("height") or 320), 320),
            "length": min(int(defaults.get("length") or 25), 25),
            "fps": min(int(defaults.get("fps") or 24), 24),
            "steps": min(int(defaults.get("steps") or 12), 8),
            "cfg": float(defaults.get("cfg") or 3.0),
        }
    if mode == "ti2v_5b":
        return {
            "width": 832,
            "height": 480,
            "length": 49,
            "fps": 24,
            "steps": min(int(defaults.get("steps") or 20), 8),
            "cfg": float(defaults.get("cfg") or 5.0),
        }
    return {
        "width": 832,
        "height": 480,
        "length": 49,
        "fps": 24,
        "steps": min(int(defaults.get("steps") or 4), 4),
        "cfg": float(defaults.get("cfg") or 1.0),
    }


def installable_missing_items(assets: list[dict[str, Any]], custom_nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    missing: list[dict[str, Any]] = []
    for item in assets:
        if not item.get("ok") and item.get("installable"):
            missing.append(
                {
                    "id": item.get("id"),
                    "label": item.get("label"),
                    "item_type": "asset",
                    "path": item.get("relative_path") or item.get("path"),
                    "download_host": item.get("download_host") or "",
                    "reason": item.get("reason"),
                    "expected_gb": item.get("expected_gb"),
                    "size_gb": item.get("size_gb"),
                    "partial_exists": item.get("partial_exists"),
                    "partial_gb": item.get("partial_gb"),
                    "remaining_gb": item.get("remaining_gb"),
                    "local_available": item.get("local_available"),
                    "local_path": item.get("local_path"),
                }
            )
    for item in custom_nodes:
        if not item.get("ok") and item.get("installable"):
            missing.append(
                {
                    "id": item.get("id"),
                    "label": item.get("label"),
                    "item_type": "custom_node",
                    "path": item.get("relative_path") or item.get("path"),
                    "download_host": item.get("download_host") or "",
                    "reason": item.get("reason"),
                    "expected_gb": item.get("expected_gb"),
                    "size_gb": item.get("size_gb"),
                    "partial_exists": item.get("partial_exists"),
                    "partial_gb": item.get("partial_gb"),
                    "remaining_gb": item.get("remaining_gb"),
                }
            )
    return missing


INSTALL_PROFILE_ASSETS = {
    "cuda-full": {
        "ti2v_5b",
        "i2v_high",
        "i2v_low",
        "t2v_high",
        "t2v_low",
        "umt5",
        "wan21_vae",
        "wan22_vae",
        "i2v_lora_high",
        "i2v_lora_low",
        "t2v_lora_high",
        "t2v_lora_low",
        "rife49",
        "realesrgan_x2",
        "ultrasharp_x4",
        "sample_keyframe",
    },
    "cuda-wan5b": {
        "ti2v_5b",
        "umt5",
        "wan22_vae",
        "rife49",
        "realesrgan_x2",
        "ultrasharp_x4",
        "sample_keyframe",
    },
    "mac-low": {"ltx_2b_095", "t5xxl_fp16", "rife49", "realesrgan_x2", "ultrasharp_x4", "sample_keyframe"},
    "mac-balanced": {"ltx_2b_095", "t5xxl_fp16", "rife49", "realesrgan_x2", "ultrasharp_x4", "sample_keyframe"},
    "mac-wan5b": {
        "ltx_2b_095",
        "t5xxl_fp16",
        "ti2v_5b",
        "umt5",
        "wan22_vae",
        "rife49",
        "realesrgan_x2",
        "ultrasharp_x4",
        "sample_keyframe",
    },
    "post-only": {"rife49", "realesrgan_x2", "ultrasharp_x4", "sample_keyframe"},
}

INSTALL_PROFILE_CUSTOM_NODES = {
    "cuda-full": {"video_helper_suite", "frame_interpolation"},
    "cuda-wan5b": {"video_helper_suite", "frame_interpolation"},
    "mac-low": {"video_helper_suite", "frame_interpolation"},
    "mac-balanced": {"video_helper_suite", "frame_interpolation"},
    "mac-wan5b": {"video_helper_suite", "frame_interpolation"},
    "post-only": {"video_helper_suite", "frame_interpolation"},
}

INSTALL_PROFILE_INFO = {
    "auto": {
        "label": "自动推荐",
        "description": "按当前系统和硬件自动选择下载档位。",
    },
    "cuda-wan5b": {
        "label": "CUDA Wan5B 保守档",
        "description": "只下载 Wan2.2 TI2V-5B、共享编码器/VAE 和后期工具，适合先跑通短镜头。",
    },
    "cuda-full": {
        "label": "CUDA 完整 Wan2.2 档",
        "description": "下载 Wan5B、A14B I2V/T2V、LoRA 和后期工具，适合 80GB+ 显存且磁盘充足。",
    },
    "mac-low": {
        "label": "Mac LTX 低档",
        "description": "Apple Silicon 优先跑通档，下载 LTX 和后期工具。",
    },
    "mac-balanced": {
        "label": "Mac LTX 均衡档",
        "description": "Apple Silicon 常用视频档，下载 LTX 和后期工具。",
    },
    "mac-wan5b": {
        "label": "Mac Wan5B 高内存实验档",
        "description": "高内存 Apple Silicon 可尝试 Wan2.2 TI2V-5B。",
    },
    "post-only": {
        "label": "仅后期工具",
        "description": "只下载 RIFE、超分权重和示例关键帧，不下载视频大模型。",
    },
}


def normalize_install_profile(profile: str | None) -> str:
    profile = (profile or "auto").strip()
    if profile in {"cuda-full", "cuda-wan5b", "mac-low", "mac-balanced", "mac-wan5b", "post-only"}:
        return profile
    return "auto"


def install_profile_for_hardware(hardware: dict[str, Any]) -> str:
    strategy = hardware.get("platform_strategy")
    tier = str(hardware.get("mac_video_tier") or "")
    if strategy == "mac_mps":
        if tier.startswith("mac_wan5b"):
            return "mac-wan5b"
        if tier in {"mac_ltx_balanced", "mac_ltx_quality"}:
            return "mac-balanced"
        return "mac-low"
    if strategy == "mac_post_only" or strategy == "post_only":
        return "post-only"
    if strategy == "cuda_wan_workflow":
        max_vram = float(hardware.get("max_vram_gb") or 0)
        return "cuda-full" if max_vram >= 80 else "cuda-wan5b"
    return "post-only"


def install_profile_options(recommended_profile: str) -> list[dict[str, Any]]:
    order = ["auto", "cuda-wan5b", "cuda-full", "mac-low", "mac-balanced", "mac-wan5b", "post-only"]
    return [
        {
            "id": profile,
            "label": INSTALL_PROFILE_INFO[profile]["label"],
            "description": INSTALL_PROFILE_INFO[profile]["description"],
            "recommended": profile == "auto" or profile == recommended_profile,
            "effective_profile": recommended_profile if profile == "auto" else profile,
        }
        for profile in order
    ]


def filter_assets_for_install_profile(assets: list[dict[str, Any]], profile: str) -> list[dict[str, Any]]:
    allowed = INSTALL_PROFILE_ASSETS.get(profile, INSTALL_PROFILE_ASSETS["cuda-full"])
    return [item for item in assets if item.get("id") in allowed]


def filter_custom_nodes_for_install_profile(custom_nodes: list[dict[str, Any]], profile: str) -> list[dict[str, Any]]:
    allowed = INSTALL_PROFILE_CUSTOM_NODES.get(profile, INSTALL_PROFILE_CUSTOM_NODES["cuda-full"])
    return [item for item in custom_nodes if item.get("id") in allowed]


def filter_registry_for_install_profile(registry_checks: list[dict[str, Any]], profile: str) -> list[dict[str, Any]]:
    allowed = INSTALL_PROFILE_ASSETS.get(profile, INSTALL_PROFILE_ASSETS["cuda-full"])
    return [item for item in registry_checks if item.get("id") in allowed]


def install_disk_plan_payload(base_dir: Path, profile: str) -> dict[str, Any]:
    if not (INSTALL_SELECTED_MODEL_ITEMS and INSTALL_PLANNED_DOWNLOAD_BYTES and INSTALL_DISK_SPACE_PLAN):
        return {
            "ok": None,
            "path": str(base_dir),
            "message": "安装器磁盘预检不可用。",
        }
    try:
        model_items = INSTALL_SELECTED_MODEL_ITEMS(profile)
        required_bytes = INSTALL_PLANNED_DOWNLOAD_BYTES(base_dir, model_items)
        plan = INSTALL_DISK_SPACE_PLAN(base_dir, required_bytes)
        plan["message"] = (
            "磁盘空间充足。"
            if plan.get("ok")
            else "磁盘空间不足，建议清理空间或更换 ComfyUI 工作目录后再下载。"
        )
        return plan
    except Exception as exc:
        return {
            "ok": None,
            "path": str(base_dir),
            "message": f"磁盘预检失败：{exc}",
        }


def install_plan_payload(
    *,
    profile: str,
    assets: list[dict[str, Any]],
    custom_nodes: list[dict[str, Any]],
    missing: list[dict[str, Any]],
    base_dir: Path | None = None,
) -> dict[str, Any]:
    selected_assets = filter_assets_for_install_profile(assets, profile)
    selected_nodes = filter_custom_nodes_for_install_profile(custom_nodes, profile)
    total_expected_gb = round(sum(float(item.get("expected_gb") or 0) for item in selected_assets), 2)
    missing_expected_gb = round(sum(float(item.get("expected_gb") or 0) for item in missing), 2)
    return {
        "profile": profile,
        "asset_count": len(selected_assets),
        "custom_node_count": len(selected_nodes),
        "total_expected_gb": total_expected_gb,
        "missing_expected_gb": missing_expected_gb,
        "disk": install_disk_plan_payload(base_dir or BASE_DIR, profile),
        "items": [
            {
                "id": item.get("id"),
                "label": item.get("label"),
                "item_type": "asset" if item in selected_assets else "custom_node",
                "ok": bool(item.get("ok")),
                "expected_gb": item.get("expected_gb"),
                "path": item.get("relative_path") or item.get("path"),
                "download_host": item.get("download_host") or "",
                "local_available": item.get("local_available"),
                "local_path": item.get("local_path"),
            }
            for item in selected_assets + selected_nodes
        ],
    }


async def install_profile_plan_payload(profile: str = "auto") -> dict[str, Any]:
    requested_profile = normalize_install_profile(profile)
    system_stats_for_profile: dict[str, Any] | None = None
    try:
        system_stats_for_profile = await comfy_get("/system_stats")
    except HTTPException:
        system_stats_for_profile = None
    hardware = build_hardware_summary(system_stats_for_profile)
    recommended_profile = install_profile_for_hardware(hardware)
    effective_profile = recommended_profile if requested_profile == "auto" else requested_profile
    comfy_paths = comfy_paths_from_system_stats(system_stats_for_profile)
    active_base_dir = Path(comfy_paths["base_dir"])
    assets = filter_assets_for_install_profile(collect_asset_checks(active_base_dir), effective_profile)
    custom_nodes = filter_custom_nodes_for_install_profile(collect_custom_node_checks(active_base_dir), effective_profile)
    missing = installable_missing_items(assets, custom_nodes)
    return {
        "requested_profile": requested_profile,
        "profile": effective_profile,
        "recommended_profile": recommended_profile,
        "profile_label": INSTALL_PROFILE_INFO.get(effective_profile, {}).get("label", effective_profile),
        "profile_description": INSTALL_PROFILE_INFO.get(effective_profile, {}).get("description", ""),
        "install_profiles": install_profile_options(recommended_profile),
        "base_dir": str(active_base_dir),
        "paths": serializable_comfy_paths(comfy_paths),
        "missing_installable": missing,
        "install_plan": install_plan_payload(
            profile=effective_profile,
            assets=assets,
            custom_nodes=custom_nodes,
            missing=missing,
            base_dir=active_base_dir,
        ),
    }


def install_command_text(command: list[str]) -> str:
    if platform.system() == "Windows":
        return subprocess.list2cmdline(command)
    return " ".join(command)


def classify_install_failure(log_lines: list[str], return_code: int | None = None, kind: str = "install") -> dict[str, Any]:
    text = "\n".join(log_lines[-80:])
    lowered = text.lower()
    title = "安装任务失败"
    message = "安装没有完成。请先复制诊断信息，再按建议处理后重试。"
    actions = [
        "点击“复制诊断信息”保存当前环境和日志。",
        "重新点击安装按钮；已下载的完整文件会跳过，.part 半截文件会断点续传。",
    ]
    category = "unknown"

    if "安装脚本不存在" in text or "script not found" in lowered or "no such file or directory" in lowered:
        category = "missing_script"
        title = "安装脚本缺失"
        message = "发布包或项目文件不完整，安装入口找不到需要执行的脚本。"
        actions = [
            "重新下载完整发布包，确认 scripts 目录没有被安全软件或手动操作删除。",
            "在发布包目录运行 python scripts/self_check.py --json，确认文件完整。",
        ]
    elif "not enough free disk space" in lowered or "磁盘空间不足" in text or "space" in lowered and "free" in lowered:
        category = "disk"
        title = "磁盘空间不足"
        message = "目标磁盘空间不够，安装已停止，未开始继续下载。"
        actions = [
            "清理目标磁盘，或把 COMFY_BASE_DIR / COMFY_INSTALL_DIR 指到更大的磁盘。",
            "也可以先切换到 Wan5B 保守档或仅后期工具，再重新安装。",
        ]
    elif any(token in lowered for token in ("could not download", "urlopen", "timed out", "timeout", "ssl", "connection reset", "403", "429", "huggingface.co", "github.com", "pypi.org", "download.pytorch.org", "下载网络预检")):
        category = "network"
        title = "下载网络不稳定"
        message = "模型或源码下载没有完成。已保留 .part 半截文件时，下次会从剩余字节继续。"
        actions = [
            "检查代理、DNS、防火墙、Hugging Face/GitHub/PyPI/PyTorch 下载源访问后重试。",
            "不要手动删除 .part 文件；重试会优先断点续传。",
            "如果持续 403/429，稍后再试或换网络。",
        ]
    elif any(token in lowered for token in ("larger than expected", "size check failed", "大小不一致", "incomplete")):
        category = "file_size"
        title = "文件大小异常"
        message = "检测到文件大小与 manifest 不一致，可能是下载被网页错误页、代理缓存或半截文件污染。"
        actions = [
            "删除日志里提到的异常文件或对应 .part 文件。",
            "重新点击一键安装，让安装器重新下载并校验大小。",
        ]
    elif any(token in lowered for token in ("pip install", "no matching distribution", "failed building wheel", "requirements.txt")):
        category = "python_dependency"
        title = "Python 依赖安装失败"
        message = "ComfyUI 或自定义节点依赖安装失败，常见原因是 Python 版本、pip 网络源或编译依赖问题。"
        actions = [
            "确认 Python 是 3.10-3.12。",
            "换网络后重试；必要时先单独运行日志里的 pip 命令。",
            "macOS 如遇编译依赖，先安装命令行工具或 Homebrew 依赖。",
        ]
    elif any(token in lowered for token in ("git clone", "git pull", "repository", "fatal:")):
        category = "git"
        title = "Git 更新/克隆失败"
        message = "源码仓库拉取失败。没有 Git 时会走 ZIP 兜底；已有 Git 但网络不稳定时可能失败。"
        actions = [
            "检查 GitHub 访问和代理设置后重试。",
            "如果已有节点目录损坏，可删除对应 custom_nodes 子目录后重试。",
        ]
    elif any(token in lowered for token in ("python", "not supported", "venv", "ensurepip")):
        category = "python_runtime"
        title = "Python/虚拟环境异常"
        message = "Python 版本或虚拟环境创建失败。"
        actions = [
            "安装 Python 3.12 后重新运行 START_WORKFLOW 启动器。",
            "如果 .venv 已损坏，可删除项目 .venv 后重新启动。",
        ]
    elif kind == "start_comfyui":
        category = "comfyui_start"
        title = "ComfyUI 启动失败"
        message = "ComfyUI 进程启动后退出或报错。"
        actions = [
            "查看日志尾部的缺失节点、模型或 Python 报错。",
            "重新执行安装/修复缺失项，然后再启动 ComfyUI。",
        ]

    return {
        "category": category,
        "title": title,
        "message": message,
        "actions": actions,
        "return_code": return_code,
    }


def attach_failure_hint(job: dict[str, Any]) -> None:
    if job.get("status") != "failed":
        return
    job["failure_hint"] = classify_install_failure(
        [str(line) for line in job.get("log", [])],
        return_code=job.get("return_code"),
        kind=str(job.get("kind") or "install"),
    )


def append_job_log(job: dict[str, Any], line: str) -> None:
    job["log"].append(line.rstrip())
    if len(job["log"]) > 800:
        job["log"] = job["log"][-800:]


def recent_jobs_payload(limit: int = 5) -> list[dict[str, Any]]:
    jobs = sorted(INSTALL_JOBS.values(), key=lambda item: item.get("created_at") or 0, reverse=True)
    payload: list[dict[str, Any]] = []
    for job in jobs[:limit]:
        payload.append(
            {
                "id": job.get("id"),
                "kind": job.get("kind"),
                "status": job.get("status"),
                "completed": job.get("completed"),
                "created_at": job.get("created_at"),
                "finished_at": job.get("finished_at"),
                "return_code": job.get("return_code"),
                "install_profile": job.get("install_profile"),
                "base_dir": job.get("base_dir"),
                "failure_hint": job.get("failure_hint"),
                "log_tail": [redact_sensitive_text(line) for line in job.get("log", [])[-25:]],
            }
        )
    return payload


def run_logged_command(job: dict[str, Any], command: list[str], *, label: str, env: dict[str, str] | None = None) -> int:
    append_job_log(job, f"{label}：{install_command_text(command)}")
    process = subprocess.Popen(
        command,
        cwd=str(WORKSPACE_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert process.stdout is not None
    for line in process.stdout:
        append_job_log(job, line)
    return process.wait()


def run_install_worker(job_id: str, command: list[str], env: dict[str, str] | None = None) -> None:
    job = INSTALL_JOBS[job_id]
    job["status"] = "running"
    job["started_at"] = now_ms()
    job["log"].append(f"启动安装：{install_command_text(command)}")
    try:
        process = subprocess.Popen(
            command,
            cwd=str(WORKSPACE_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
        assert process.stdout is not None
        for line in process.stdout:
            job["log"].append(line.rstrip())
            if len(job["log"]) > 600:
                job["log"] = job["log"][-600:]
        return_code = process.wait()
        job["return_code"] = return_code
        job["status"] = "success" if return_code == 0 else "failed"
        job["completed"] = True
        job["finished_at"] = now_ms()
        if return_code == 0:
            job["log"].append("安装/校验完成。请重启 ComfyUI，让新节点和模型列表重新加载。")
        else:
            job["log"].append(f"安装脚本退出码：{return_code}")
            attach_failure_hint(job)
    except Exception as exc:
        job["status"] = "failed"
        job["completed"] = True
        job["finished_at"] = now_ms()
        job["error"] = str(exc)
        job["log"].append(f"安装失败：{exc}")
        attach_failure_hint(job)


def sync_comfy_ready_url(timeout: float = 120.0) -> str | None:
    global COMFY_URL
    deadline = time.time() + timeout
    while time.time() < deadline:
        for url in common_comfy_urls():
            try:
                response = httpx.get(f"{url}/system_stats", timeout=2.0)
                if response.status_code == 200:
                    COMFY_URL = url
                    return url
            except Exception:
                pass
        time.sleep(1.0)
    return None


def comfy_start_command() -> list[str]:
    runtime = comfy_runtime_info()
    main_py = Path(str(runtime["main_py"]))
    python = Path(str(runtime["python"]))
    if not main_py.exists():
        raise RuntimeError(f"ComfyUI 未安装：{main_py}")
    if not python.exists():
        raise RuntimeError(f"ComfyUI Python 未就绪：{python}")
    return [
        str(python),
        str(main_py),
        "--listen",
        comfy_listen_host(),
        "--port",
        str(comfy_port()),
        "--base-directory",
        str(BASE_DIR),
        "--input-directory",
        str(INPUT_DIR),
        "--output-directory",
        str(OUTPUT_DIR),
        "--user-directory",
        str(BASE_DIR / "user"),
    ]


def comfy_start_cwd() -> Path:
    runtime = comfy_runtime_info()
    main_py = Path(str(runtime.get("main_py") or COMFY_INSTALL_DIR / "main.py"))
    if main_py.exists():
        return main_py.parent
    return COMFY_INSTALL_DIR


def monitor_comfyui_process(job_id: str, process: subprocess.Popen[str], *, update_terminal_status: bool) -> None:
    job = INSTALL_JOBS.get(job_id)
    try:
        assert process.stdout is not None
        for line in process.stdout:
            if job:
                append_job_log(job, line)
        return_code = process.wait()
        if not job:
            return
        job["return_code"] = return_code
        append_job_log(job, f"ComfyUI 已退出，退出码：{return_code}")
        if update_terminal_status or not job.get("completed"):
            job["status"] = "stopped" if return_code == 0 else "failed"
            job["completed"] = True
            job["finished_at"] = now_ms()
            attach_failure_hint(job)
    except Exception as exc:
        if job and (update_terminal_status or not job.get("completed")):
            job["status"] = "failed"
            job["completed"] = True
            job["finished_at"] = now_ms()
            job["error"] = str(exc)
            append_job_log(job, f"ComfyUI 日志监控失败：{exc}")
            attach_failure_hint(job)


def launch_comfyui_background(job_id: str, command: list[str], cwd: Path | None = None) -> None:
    global COMFY_PROCESS
    creationflags = 0
    if platform.system() == "Windows":
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    COMFY_PROCESS = subprocess.Popen(
        command,
        cwd=str(cwd or comfy_start_cwd()),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    thread = threading.Thread(
        target=monitor_comfyui_process,
        args=(job_id, COMFY_PROCESS),
        kwargs={"update_terminal_status": False},
        daemon=True,
    )
    thread.start()


def launch_comfy_desktop_background(job_id: str, executable: Path) -> None:
    job = INSTALL_JOBS[job_id]
    command = [str(executable)]
    cwd = executable.parent if executable.is_file() else executable
    if platform.system() == "Darwin" and executable.suffix == ".app":
        command = ["open", str(executable)]
        cwd = executable.parent
    append_job_log(job, f"检测到 ComfyUI Desktop：{executable}")
    append_job_log(job, "已尝试启动 Desktop，正在等待 /system_stats。")
    process = subprocess.Popen(
        command,
        cwd=str(cwd),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    job["desktop_process_id"] = process.pid
    thread = threading.Thread(target=wait_for_comfyui_ready_worker, args=(job_id,), daemon=True)
    thread.start()


def mark_job_success(job: dict[str, Any], message: str) -> None:
    append_job_log(job, message)
    job["status"] = "success"
    job["completed"] = True
    job["finished_at"] = now_ms()


DOWNLOAD_HOST_LABELS = {
    "github.com": "GitHub",
    "huggingface.co": "Hugging Face",
    "pypi.org": "PyPI",
    "download.pytorch.org": "PyTorch 下载源",
}


def host_label(host: str) -> str:
    return DOWNLOAD_HOST_LABELS.get(host, host)


def effective_preflight_host(host: str) -> str:
    if host == "huggingface.co":
        endpoint = effective_hf_endpoint()
        if endpoint:
            return urlparse(endpoint).hostname or host
    if host == "pypi.org":
        pip_index = effective_pip_index_url()
        if pip_index:
            return urlparse(pip_index).hostname or host
    return host


def effective_comfy_install_backend(backend: str) -> str:
    normalized = (backend or "auto").strip().lower()
    if normalized != "auto":
        return normalized
    if COMFYUI_EFFECTIVE_BACKEND:
        try:
            return str(COMFYUI_EFFECTIVE_BACKEND(normalized)).strip().lower()
        except Exception:
            pass
    if platform.system() == "Darwin":
        return "mps"
    if shutil.which("nvidia-smi"):
        return "cuda"
    return "cpu"


def comfy_install_uses_pytorch_host(backend: str) -> bool:
    return effective_comfy_install_backend(backend) in {"cuda", "cpu"}


def required_download_hosts_for_full_setup(
    *,
    profile: str,
    base_dir: Path,
    backend: str,
    skip_comfy_install: bool,
    use_local_comfy_python: bool,
) -> set[str]:
    hosts: set[str] = set()
    if not skip_comfy_install:
        hosts.update({"github.com", "pypi.org"})
        if comfy_install_uses_pytorch_host(backend):
            hosts.add("download.pytorch.org")

    selected_assets = filter_assets_for_install_profile(collect_asset_checks(base_dir), profile)
    selected_nodes = filter_custom_nodes_for_install_profile(collect_custom_node_checks(base_dir), profile)
    missing_assets = [item for item in selected_assets if not item.get("ok")]
    for item in missing_assets:
        if item.get("local_available"):
            continue
        host = str(item.get("download_host") or "")
        if host:
            hosts.add(host)
    missing_nodes = [item for item in selected_nodes if not item.get("ok")]
    for item in missing_nodes:
        host = str(item.get("download_host") or "")
        if host:
            hosts.add(host)
    if use_local_comfy_python and (missing_nodes or (selected_nodes and not skip_comfy_install)):
        hosts.add("pypi.org")
    return hosts


def required_download_hosts_for_workflow_assets(
    *,
    profile: str,
    base_dir: Path,
    use_local_comfy_python: bool,
    repair_needed: bool = False,
) -> set[str]:
    hosts: set[str] = set()
    selected_assets = filter_assets_for_install_profile(collect_asset_checks(base_dir), profile)
    selected_nodes = filter_custom_nodes_for_install_profile(collect_custom_node_checks(base_dir), profile)
    missing_assets = [item for item in selected_assets if not item.get("ok")]
    for item in missing_assets:
        if item.get("local_available"):
            continue
        host = str(item.get("download_host") or "")
        if host:
            hosts.add(host)
    missing_nodes = [item for item in selected_nodes if not item.get("ok")]
    for item in missing_nodes:
        host = str(item.get("download_host") or "")
        if host:
            hosts.add(host)
    if use_local_comfy_python and selected_nodes and (missing_assets or missing_nodes or repair_needed):
        hosts.add("pypi.org")
    return hosts


def run_download_network_preflight(job: dict[str, Any], required_hosts: set[str]) -> None:
    required_hosts = {effective_preflight_host(host) for host in required_hosts if host}
    if not required_hosts:
        append_job_log(job, "步骤 0/3：没有需要访问外网的缺失下载项，跳过下载网络预检。")
        job["network_preflight"] = {"status": "skipped", "required_hosts": []}
        return
    if not BUILD_PREREQUISITE_REPORT:
        append_job_log(job, "下载网络预检模块不可用，跳过网络预检。")
        return
    required_labels = ", ".join(host_label(host) for host in sorted(required_hosts))
    append_job_log(job, f"步骤 0/3：检查本次需要的下载源：{required_labels}。")
    report = call_with_download_env(BUILD_PREREQUISITE_REPORT, WORKSPACE_DIR, BASE_DIR, COMFY_INSTALL_DIR, include_network=True)
    network_check = next((item for item in report.get("checks", []) if item.get("id") == "network"), None)
    job["network_preflight"] = network_check or {"status": "unknown", "message": "未返回网络检查结果"}
    job["network_preflight"]["required_hosts"] = sorted(required_hosts)
    if not network_check:
        append_job_log(job, "下载网络预检未返回结果，继续安装。")
        return
    append_job_log(job, str(network_check.get("message") or "下载网络预检完成。"))
    reachable = network_check.get("reachable") or {}
    missing_required = [host for host in sorted(required_hosts) if reachable.get(host) is not True]
    job["network_preflight"]["missing_required_hosts"] = missing_required
    if missing_required:
        action = str(network_check.get("action") or "请检查网络后重试。")
        append_job_log(job, action)
        missing_labels = ", ".join(host_label(host) for host in missing_required)
        raise RuntimeError(f"下载网络预检未通过：本次需要的下载源不可达：{missing_labels}")


def required_download_hosts_for_comfy_install(backend: str) -> set[str]:
    hosts = {"github.com", "pypi.org"}
    if comfy_install_uses_pytorch_host(backend):
        hosts.add("download.pytorch.org")
    return hosts


def wait_for_comfyui_ready_worker(job_id: str, timeout: float = 180.0) -> None:
    global COMFY_START_JOB_ID
    job = INSTALL_JOBS[job_id]
    job["status"] = "running"
    job["started_at"] = job.get("started_at") or now_ms()
    try:
        ready_url = sync_comfy_ready_url(timeout=timeout)
        if ready_url:
            job["ready_url"] = ready_url
            mark_job_success(job, f"ComfyUI 已就绪：{ready_url}")
            return
        if job.get("completed"):
            return
        raise RuntimeError(f"ComfyUI 正在启动，但 {int(timeout)} 秒内没有响应 /system_stats。")
    except Exception as exc:
        if not job.get("completed"):
            job["status"] = "failed"
            job["completed"] = True
            job["finished_at"] = now_ms()
            job["error"] = str(exc)
            append_job_log(job, f"ComfyUI 启动失败：{exc}")
            attach_failure_hint(job)
    finally:
        if job.get("completed") and COMFY_START_JOB_ID == job_id:
            COMFY_START_JOB_ID = None


def run_full_setup_worker(
    job_id: str,
    *,
    backend: str,
    profile: str,
    base_dir: Path,
    dry_run: bool,
    skip_comfy_install: bool = False,
    use_local_comfy_python: bool = True,
) -> None:
    job = INSTALL_JOBS[job_id]
    job["status"] = "running"
    job["started_at"] = now_ms()
    try:
        install_script = WORKSPACE_DIR / "scripts" / "install_comfyui.py"
        assets_script = WORKSPACE_DIR / "scripts" / "install_workflow_assets.py"
        if not install_script.exists():
            raise RuntimeError(f"安装脚本不存在：{install_script}")
        if not assets_script.exists():
            raise RuntimeError(f"安装脚本不存在：{assets_script}")

        resolved_profile = normalize_install_profile(profile)
        if resolved_profile == "auto":
            resolved_profile = install_profile_for_hardware(build_hardware_summary(None))
        job["install_profile"] = resolved_profile
        job["download_settings"] = download_settings_payload()

        if not dry_run:
            required_hosts = required_download_hosts_for_full_setup(
                profile=resolved_profile,
                base_dir=base_dir,
                backend=backend,
                skip_comfy_install=skip_comfy_install,
                use_local_comfy_python=use_local_comfy_python,
            )
            run_download_network_preflight(job, required_hosts)

        if skip_comfy_install:
            job["source_install_skipped"] = True
            if (job.get("paths") or {}).get("source") == "running_comfyui":
                append_job_log(job, "步骤 1/3：检测到 ComfyUI 已在运行，跳过源码版 ComfyUI 安装/更新。")
                append_job_log(job, "将使用当前运行中 ComfyUI 的 active base 安装模型和节点，避免额外创建一份源码版 ComfyUI。")
            else:
                append_job_log(job, f"步骤 1/3：检测到可启动的 {job.get('runtime_label') or 'ComfyUI runtime'}，跳过额外源码安装。")
                append_job_log(job, "将直接复用该 runtime 的 Python 安装节点依赖，并在最后启动 ComfyUI。")
        else:
            install_command = [
                sys.executable,
                "-u",
                str(install_script),
                "--base-dir",
                str(base_dir),
                "--install-dir",
                str(COMFY_INSTALL_DIR),
                "--backend",
                backend,
            ]
            if dry_run:
                install_command.append("--dry-run")
            append_job_log(job, "步骤 1/3：安装或更新 ComfyUI。")
            return_code = run_logged_command(job, install_command, label="ComfyUI 安装", env=download_subprocess_env())
            if return_code != 0:
                raise RuntimeError(f"ComfyUI 安装失败，退出码：{return_code}")

        assets_command = [
            sys.executable,
            "-u",
            str(assets_script),
            "--base-dir",
            str(base_dir),
            "--profile",
            resolved_profile,
        ]
        python = comfy_venv_python()
        if use_local_comfy_python and python.exists():
            assets_command.extend(["--comfy-python", str(python)])
        elif not use_local_comfy_python:
            append_job_log(job, "检测到运行中的 ComfyUI 可能来自 Desktop 或外部目录；不会把本项目 venv 当作节点 Python。")
            append_job_log(job, "如果节点下载后未加载，请在 ComfyUI Desktop/管理器里安装节点依赖并重启 ComfyUI。")
        if dry_run:
            assets_command.append("--dry-run")
        append_job_log(job, f"步骤 2/3：安装当前档位资源：{resolved_profile}。")
        return_code = run_logged_command(job, assets_command, label="模型/节点安装", env=download_subprocess_env())
        if return_code != 0:
            raise RuntimeError(f"模型/节点安装失败，退出码：{return_code}")

        if dry_run:
            append_job_log(job, "dry-run 完成：没有下载模型，也没有写入 ComfyUI。")
            job["status"] = "success"
            job["completed"] = True
            job["finished_at"] = now_ms()
            return

        append_job_log(job, "步骤 3/3：启动 ComfyUI 并等待服务就绪。")
        ready_url = sync_comfy_ready_url(timeout=3.0)
        if ready_url:
            job["restart_required"] = True
            append_job_log(job, f"检测到 ComfyUI 已在运行：{ready_url}")
            append_job_log(job, "如果本次刚安装了新节点或新模型，请重启 ComfyUI 后再运行生成链路测试。")
            job["status"] = "success"
            job["completed"] = True
            job["finished_at"] = now_ms()
            return

        command = comfy_start_command()
        job["start_command"] = install_command_text(command)
        launch_comfyui_background(job_id, command)
        ready_url = sync_comfy_ready_url(timeout=180.0)
        if not ready_url:
            raise RuntimeError("ComfyUI 已尝试启动，但 180 秒内没有响应 /system_stats。")

        append_job_log(job, f"ComfyUI 已启动：{ready_url}")
        append_job_log(job, "一键准备完成。下一步点击“生成链路测试”，确认真实视频生成链路。")
        job["status"] = "success"
        job["completed"] = True
        job["finished_at"] = now_ms()
    except Exception as exc:
        job["status"] = "failed"
        job["completed"] = True
        job["finished_at"] = now_ms()
        job["error"] = str(exc)
        append_job_log(job, f"一键准备失败：{exc}")
        attach_failure_hint(job)


def run_json_command(command: list[str], timeout: int = 180, env: dict[str, str] | None = None) -> dict[str, Any]:
    result = subprocess.run(
        command,
        cwd=str(WORKSPACE_DIR),
        env=env,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        timeout=timeout,
    )
    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()
    parsed: dict[str, Any] | None = None
    if stdout:
        try:
            parsed = json.loads(stdout)
        except json.JSONDecodeError:
            parsed = None
    return {
        "command": install_command_text(command),
        "return_code": result.returncode,
        "ok": bool(parsed.get("ok")) if isinstance(parsed, dict) and "ok" in parsed else result.returncode == 0,
        "json": parsed,
        "stdout_tail": stdout[-2000:],
        "stderr_tail": stderr[-2000:],
    }


def run_self_test_worker(job_id: str) -> None:
    job = INSTALL_JOBS[job_id]
    job["status"] = "running"
    job["started_at"] = now_ms()
    job["log"].append("开始本机自检：不会下载模型，也不会启动真实生成。")
    commands = [
        [
            sys.executable,
            str(WORKSPACE_DIR / "scripts" / "prerequisite_doctor.py"),
            "--json",
            "--base-dir",
            str(BASE_DIR),
            "--install-dir",
            str(COMFY_INSTALL_DIR),
        ],
        [sys.executable, str(WORKSPACE_DIR / "scripts" / "self_check.py"), "--json"],
    ]
    command_env = download_subprocess_env()
    command_env.pop("COMFY_URL", None)
    command_env["PYTHONDONTWRITEBYTECODE"] = "1"
    results: list[dict[str, Any]] = []
    try:
        for command in commands:
            job["log"].append(f"运行：{install_command_text(command)}")
            result = run_json_command(command, env=command_env)
            results.append(result)
            status_text = "通过" if result["ok"] else "未通过"
            job["log"].append(f"{Path(command[1]).name}：{status_text}，退出码 {result['return_code']}")
            if result.get("stderr_tail"):
                job["log"].append(str(result["stderr_tail"])[-1000:])
            if not result["ok"] and result.get("stdout_tail"):
                job["log"].append(str(result["stdout_tail"])[-1000:])
            if len(job["log"]) > 600:
                job["log"] = job["log"][-600:]
        job["self_test"] = {
            "ok": all(item["ok"] for item in results),
            "results": results,
        }
        job["status"] = "success" if job["self_test"]["ok"] else "failed"
        job["completed"] = True
        job["finished_at"] = now_ms()
        if job["status"] == "success":
            job["log"].append("本机自检通过。")
        else:
            job["log"].append("本机自检发现问题；请点击“复制诊断信息”保存结果。")
            attach_failure_hint(job)
    except Exception as exc:
        job["status"] = "failed"
        job["completed"] = True
        job["finished_at"] = now_ms()
        job["error"] = str(exc)
        job["log"].append(f"本机自检失败：{exc}")
        attach_failure_hint(job)


def venv_python_for(venv_root: Path) -> Path:
    if platform.system() == "Windows":
        return venv_root / "Scripts" / "python.exe"
    return venv_root / "bin" / "python"


def source_comfy_python() -> Path:
    return venv_python_for(COMFY_INSTALL_DIR / ".venv")


def base_comfy_python() -> Path:
    return venv_python_for(BASE_DIR / ".venv")


def path_exists(path: Path | None) -> bool:
    return bool(path and path.exists())


def unique_existing_paths(paths: list[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        try:
            resolved = path.expanduser().resolve()
        except Exception:
            continue
        key = str(resolved).lower() if platform.system() == "Windows" else str(resolved)
        if key in seen:
            continue
        seen.add(key)
        unique.append(resolved)
    return unique


def comfy_desktop_roots() -> list[Path]:
    candidates: list[Path] = []
    for env_name in ("COMFY_DESKTOP_DIR", "COMFY_DESKTOP_ROOT"):
        configured = os.environ.get(env_name)
        if configured:
            candidates.append(Path(configured))
    for env_name in ("COMFY_DESKTOP_EXE", "COMFY_DESKTOP_APP"):
        configured = os.environ.get(env_name)
        if configured:
            path = Path(configured)
            candidates.append(path if path.is_dir() else path.parent)
    candidates.extend(
        [
            BASE_DIR / "ComfyUI",
            WORKSPACE_DIR.parent / "ComfyUI",
            COMFY_INSTALL_DIR,
            WORKSPACE_DIR / "ComfyUI",
        ]
    )
    if platform.system() == "Darwin":
        candidates.extend(
            [
                Path("/Applications/ComfyUI.app"),
                BASE_DIR / "ComfyUI.app",
                WORKSPACE_DIR.parent / "ComfyUI.app",
            ]
        )
    return unique_existing_paths(candidates)


def comfy_desktop_executable() -> Path | None:
    env_path = os.environ.get("COMFY_DESKTOP_EXE") or os.environ.get("COMFY_DESKTOP_APP")
    if env_path:
        candidate = Path(env_path).expanduser()
        if candidate.exists():
            return candidate.resolve()
    names = ["ComfyUI.exe", "ComfyUI"]
    if platform.system() == "Darwin":
        names = ["ComfyUI.app", "ComfyUI"]
    for root in comfy_desktop_roots():
        for name in names:
            candidate = root / name
            if candidate.exists():
                return candidate.resolve()
        if root.name.endswith(".app") and root.exists():
            return root.resolve()
    return None


def bundled_comfy_main_py() -> Path | None:
    candidates: list[Path] = []
    for root in comfy_desktop_roots():
        candidates.extend(
            [
                root / "resources" / "ComfyUI" / "main.py",
                root / "Contents" / "Resources" / "ComfyUI" / "main.py",
                root / "Comfy Desktop" / "resources" / "ComfyUI" / "main.py",
            ]
        )
    for candidate in unique_existing_paths(candidates):
        if candidate.exists():
            return candidate
    return None


def comfy_runtime_info() -> dict[str, Any]:
    source_main = (COMFY_INSTALL_DIR / "main.py").expanduser().resolve()
    source_python = source_comfy_python().expanduser().resolve()
    bundled_main = bundled_comfy_main_py()
    base_python = base_comfy_python().expanduser().resolve()
    desktop_exe = comfy_desktop_executable()

    if source_main.exists() and source_python.exists():
        return {
            "kind": "source",
            "label": "源码版 ComfyUI",
            "main_py": source_main,
            "python": source_python,
            "cwd": source_main.parent,
            "source_ready": True,
            "desktop_available": bool(desktop_exe),
            "desktop_exe": str(desktop_exe or ""),
            "bundled_main": str(bundled_main or ""),
        }
    if bundled_main and base_python.exists():
        return {
            "kind": "desktop_runtime",
            "label": "ComfyUI Desktop runtime",
            "main_py": bundled_main,
            "python": base_python,
            "cwd": bundled_main.parent,
            "source_ready": True,
            "desktop_available": bool(desktop_exe),
            "desktop_exe": str(desktop_exe or ""),
            "bundled_main": str(bundled_main),
        }
    if bundled_main:
        return {
            "kind": "desktop_runtime_missing_python",
            "label": "ComfyUI Desktop runtime",
            "main_py": bundled_main,
            "python": base_python,
            "cwd": bundled_main.parent,
            "source_ready": False,
            "desktop_available": bool(desktop_exe),
            "desktop_exe": str(desktop_exe or ""),
            "bundled_main": str(bundled_main),
        }
    return {
        "kind": "missing",
        "label": "源码版 ComfyUI",
        "main_py": source_main,
        "python": source_python,
        "cwd": COMFY_INSTALL_DIR,
        "source_ready": False,
        "desktop_available": bool(desktop_exe),
        "desktop_exe": str(desktop_exe or ""),
        "bundled_main": "",
    }


def comfy_venv_python() -> Path:
    runtime = comfy_runtime_info()
    return Path(str(runtime["python"]))


def comfy_port() -> int:
    parsed = urlparse(COMFY_URL)
    if parsed.port:
        return int(parsed.port)
    return 8000


def comfy_listen_host() -> str:
    parsed = urlparse(COMFY_URL)
    return parsed.hostname or "127.0.0.1"


def bootstrap_status_payload(
    comfy_connected: bool,
    comfy_error: str = "",
    paths: dict[str, Any] | None = None,
    request: Request | None = None,
) -> dict[str, Any]:
    runtime = comfy_runtime_info()
    main_py = Path(str(runtime["main_py"]))
    python = Path(str(runtime["python"]))
    paths = paths or configured_comfy_paths()
    if COMFYUI_DISK_PLAN:
        try:
            install_disk = COMFYUI_DISK_PLAN(COMFY_INSTALL_DIR, "auto")
            install_disk["message"] = "安装空间充足。" if install_disk.get("ok") else "安装空间不足，建议清理磁盘或调整 COMFY_INSTALL_DIR。"
        except Exception as exc:
            install_disk = {"ok": None, "message": f"安装空间预检失败：{exc}", "path": str(COMFY_INSTALL_DIR)}
    else:
        install_disk = {"ok": None, "message": "安装空间预检不可用。", "path": str(COMFY_INSTALL_DIR)}
    prerequisites = (
        call_with_download_env(BUILD_PREREQUISITE_REPORT, WORKSPACE_DIR, BASE_DIR, COMFY_INSTALL_DIR, include_network=False)
        if BUILD_PREREQUISITE_REPORT
        else {
            "ok": None,
            "blocked_count": 0,
            "warning_count": 0,
            "checks": [],
            "network_checked": False,
        }
    )
    return {
        "base_dir": str(BASE_DIR),
        "active_base_dir": str(paths["base_dir"]),
        "paths": serializable_comfy_paths(paths),
        "install_dir": str(COMFY_INSTALL_DIR),
        "install_disk": install_disk,
        "prerequisites": prerequisites,
        "download_settings": download_settings_payload(),
        "service": service_config_payload(request, include_sensitive=True),
        "comfy_url": COMFY_URL,
        "comfy_url_configured": COMFY_URL_CONFIGURED,
        "comfy_candidate_urls": common_comfy_urls(),
        "comfy_connected": comfy_connected,
        "comfy_error": comfy_error,
        "comfy_repo_exists": main_py.exists(),
        "comfy_main": str(main_py),
        "runtime_kind": runtime.get("kind"),
        "runtime_label": runtime.get("label"),
        "runtime_source_ready": bool(runtime.get("source_ready")),
        "desktop_available": bool(runtime.get("desktop_available")),
        "desktop_exe": str(runtime.get("desktop_exe") or ""),
        "bundled_main": str(runtime.get("bundled_main") or ""),
        "venv_python": str(python),
        "venv_ready": python.exists(),
        "git_ready": bool(shutil.which("git")),
        "python": sys.version.split()[0],
        "platform": platform.system(),
        "can_install_comfyui": True,
        "can_start_comfyui": bool(runtime.get("source_ready") or runtime.get("desktop_available")),
        "running_from_launcher": True,
    }


def run_comfyui_worker(job_id: str, command: list[str]) -> None:
    global COMFY_START_JOB_ID
    job = INSTALL_JOBS[job_id]
    job["status"] = "running"
    job["started_at"] = now_ms()
    job["log"].append(f"启动 ComfyUI：{install_command_text(command)}")
    try:
        launch_comfyui_background(job_id, command)
        wait_for_comfyui_ready_worker(job_id)
    except Exception as exc:
        if not job.get("completed"):
            job["status"] = "failed"
            job["completed"] = True
            job["finished_at"] = now_ms()
            job["error"] = str(exc)
            job["log"].append(f"ComfyUI 启动失败：{exc}")
            attach_failure_hint(job)
    finally:
        if job.get("completed"):
            COMFY_START_JOB_ID = None


def run_deflicker(source: Path, paths: dict[str, Any] | None = None) -> dict[str, Any]:
    if source.suffix.lower() not in VIDEO_EXTENSIONS:
        raise HTTPException(status_code=400, detail="闪烁修复只支持视频文件")

    paths = paths or configured_comfy_paths()
    output_dir = Path(paths["output_dir"]) / "wan22_frontend"
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"Deflicker_{time.strftime('%Y-%m-%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.mp4"

    filter_chain = "deflicker=s=8:m=pm,hqdn3d=1.2:1.2:4:4,format=yuv420p"
    command = [
        find_ffmpeg(),
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "0:a?",
        "-vf",
        filter_chain,
        "-c:v",
        "libx264",
        "-preset",
        "medium",
        "-crf",
        "18",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
        str(output),
    ]

    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        if output.exists():
            output.unlink()
        detail = result.stderr.strip()[-2000:] or "ffmpeg 闪烁修复失败"
        raise HTTPException(status_code=500, detail=detail)

    return output_media_item(output, paths)


@app.get("/")
async def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> Response:
    return Response(status_code=204)


def health_payload(request: Request | None = None) -> dict[str, Any]:
    return {
        "ok": True,
        "app_version": APP_VERSION,
        "workspace_dir": str(WORKSPACE_DIR),
        "base_dir": str(BASE_DIR),
        "service": service_config_payload(request),
        "comfy_url": COMFY_URL,
        "comfy_url_configured": COMFY_URL_CONFIGURED,
        "comfy_candidate_urls": common_comfy_urls(),
        "process_id": os.getpid(),
        "started_at": STARTED_AT,
        "uptime_seconds": round(time.time() - STARTED_AT, 1),
    }


@app.get("/api/health")
async def health(request: Request) -> dict[str, Any]:
    return health_payload(request)


@app.get("/api/client-config")
async def client_config(request: Request) -> dict[str, Any]:
    return {
        "ok": True,
        "service": service_config_payload(request, include_sensitive=True),
    }


@app.post("/api/service-config")
async def save_service_config(request: Request, payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    data = payload.get("service") if isinstance(payload.get("service"), dict) else payload
    mode = normalize_service_mode(str(data.get("mode") or ""))
    if not mode:
        raise HTTPException(status_code=400, detail="请选择服务端、客户端或双模式。")
    try:
        server_url = normalize_server_url(str(data.get("server_url") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    access_token = str(data.get("access_token") or "").strip()
    config = read_local_config()
    previous_mode = service_config_payload(request, include_sensitive=True)

    if mode == "client" and not server_url:
        raise HTTPException(status_code=400, detail="客户端模式需要填写服务端地址。")
    if mode in {"server", "both"} and not access_token:
        access_token = str(config.get("access_token") or "") or uuid.uuid4().hex

    config["node_mode"] = mode
    config["bind_host"] = "0.0.0.0" if mode in {"server", "both"} else "127.0.0.1"
    if server_url:
        config["server_url"] = server_url
    else:
        config.pop("server_url", None)
    if access_token:
        config["access_token"] = access_token
    else:
        config.pop("access_token", None)
    write_local_config(config)

    current = service_config_payload(request, include_sensitive=True)
    restart_required = previous_mode.get("mode") != current.get("mode") or previous_mode.get("bind_host") != current.get("bind_host")
    return {
        "ok": True,
        "service": current,
        "restart_required": restart_required,
        "message": "服务模式已保存。" + (" 重启 start.bat 后监听地址会生效。" if restart_required else ""),
    }


@app.get("/api/status")
async def status() -> dict[str, Any]:
    try:
        system_stats = await comfy_get("/system_stats")
        queue = await comfy_get("/queue")
    except HTTPException as exc:
        return {
            "connected": False,
            "comfy_url": COMFY_URL,
            "comfy_url_configured": COMFY_URL_CONFIGURED,
            "comfy_candidate_urls": common_comfy_urls(),
            "error": str(exc.detail),
            "queue_running": None,
            "queue_pending": None,
        }
    device = (system_stats.get("devices") or [{}])[0]
    return {
        "connected": True,
        "comfy_url": COMFY_URL,
        "comfy_url_configured": COMFY_URL_CONFIGURED,
        "comfy_candidate_urls": common_comfy_urls(),
        "comfyui_version": system_stats.get("system", {}).get("comfyui_version"),
        "pytorch_version": system_stats.get("system", {}).get("pytorch_version"),
        "device_name": device.get("name"),
        "vram_total_gb": round((device.get("vram_total") or 0) / 1024**3, 1),
        "vram_free_gb": round((device.get("vram_free") or 0) / 1024**3, 1),
        "queue_running": len(queue.get("queue_running") or []),
        "queue_pending": len(queue.get("queue_pending") or []),
    }


@app.get("/api/environment")
async def environment() -> dict[str, Any]:
    os_info = {
        "name": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
    }

    system_stats: dict[str, Any] | None = None
    object_info: dict[str, Any] | None = None
    comfy_error = ""
    queue_error = ""
    queue_info: dict[str, Any] = {"running": None, "pending": None}

    try:
        system_stats = await comfy_get("/system_stats")
    except HTTPException as exc:
        comfy_error = str(exc.detail)

    try:
        object_info = await comfy_get("/object_info")
    except HTTPException as exc:
        if not comfy_error:
            comfy_error = str(exc.detail)

    try:
        queue = await comfy_get("/queue")
        queue_info = {
            "running": len(queue.get("queue_running") or []),
            "pending": len(queue.get("queue_pending") or []),
        }
    except HTTPException as exc:
        queue_error = str(exc.detail)

    comfy_paths = comfy_paths_from_system_stats(system_stats)
    active_base_dir = Path(comfy_paths["base_dir"])
    assets = collect_asset_checks(active_base_dir)
    custom_nodes = collect_custom_node_checks(active_base_dir)
    node_checks = collect_node_checks(object_info)
    registry_checks = collect_model_registry_checks(object_info)

    tool_checks = ffmpeg_tool_checks()
    hardware = build_hardware_summary(system_stats)
    install_profile = install_profile_for_hardware(hardware)
    install_assets = filter_assets_for_install_profile(assets, install_profile)
    install_custom_nodes = filter_custom_nodes_for_install_profile(custom_nodes, install_profile)
    install_registry_checks = filter_registry_for_install_profile(registry_checks, install_profile)
    diagnostics = build_load_diagnostics(
        paths=comfy_paths,
        assets=install_assets,
        custom_nodes=install_custom_nodes,
        node_checks=node_checks,
        registry_checks=install_registry_checks,
        object_info=object_info,
    )
    mac_info = hardware.get("mac") or {}
    if mac_info.get("is_macos") and mac_info.get("apple_silicon"):
        if not (hardware.get("torch") or {}).get("available"):
            diagnostics.insert(
                0,
                {
                    "level": "warn",
                    "title": "前端 Python 未安装 torch",
                    "message": "这不代表 ComfyUI 不能使用 MPS。系统会单独检测 ComfyUI venv 的 torch.backends.mps 状态。",
                },
            )
        if not (hardware.get("comfy_torch") or {}).get("exists"):
            diagnostics.insert(
                0,
                {
                    "level": "warn",
                    "title": "ComfyUI Python 尚未确认",
                    "message": "未找到前端配置的 ComfyUI venv；若使用 ComfyUI Desktop，请先启动后刷新，系统会从 /system_stats 和 /object_info 继续预检。",
                },
            )
    comfy_system = (system_stats or {}).get("system", {})
    comfy_info = {
        "url": COMFY_URL,
        "url_configured": COMFY_URL_CONFIGURED,
        "candidate_urls": common_comfy_urls(),
        "connected": system_stats is not None,
        "error": comfy_error,
        "queue_error": queue_error,
        "queue": queue_info,
        "comfyui_version": comfy_system.get("comfyui_version"),
        "pytorch_version": comfy_system.get("pytorch_version"),
        "python_version": comfy_system.get("python_version"),
    }
    recommendations = model_recommendations(
        os_info=os_info,
        hardware=hardware,
        assets=assets,
        custom_nodes=custom_nodes,
        tool_checks=tool_checks,
        node_checks=node_checks,
        base_dir=active_base_dir,
    )
    model_options = workflow_model_options(
        os_info=os_info,
        hardware=hardware,
        assets=assets,
        custom_nodes=custom_nodes,
        tool_checks=tool_checks,
        node_checks=node_checks,
        base_dir=active_base_dir,
    )
    missing_installable = installable_missing_items(install_assets, install_custom_nodes)
    repair_needed = any(item.get("level") == "blocked" for item in diagnostics)
    if repair_needed and not missing_installable:
        missing_installable.append(
            {
                "id": "repair_loaded_assets",
                "label": "修复已下载但未加载的节点/模型",
                "item_type": "repair",
                "path": str(active_base_dir),
                "download_host": "",
                "reason": "重新校验文件、安装自定义节点 requirements，并提示重启 ComfyUI。",
            }
        )
    core_steps = {"试镜头", "正式片段", "闪烁修复", "插帧", "清晰度增强"}
    blocked = [
        item
        for item in recommendations
        if item["step"] in core_steps and item["status"] == "blocked"
    ]

    return {
        "ok": bool(comfy_info["connected"] and not blocked),
        "base_dir": str(active_base_dir),
        "configured_base_dir": str(BASE_DIR),
        "workspace_dir": str(WORKSPACE_DIR),
        "paths": serializable_comfy_paths(comfy_paths),
        "service": service_config_payload(),
        "os": os_info,
        "comfy": comfy_info,
        "hardware": hardware,
        "install_profile": install_profile,
        "recommended_install_profile": install_profile,
        "install_profiles": install_profile_options(install_profile),
        "download_settings": download_settings_payload(),
        "assets": assets,
        "custom_nodes": custom_nodes,
        "nodes": node_checks,
        "model_registry": registry_checks,
        "diagnostics": diagnostics,
        "tools": tool_checks,
        "recommendations": recommendations,
        "model_options": model_options,
        "missing_installable": missing_installable,
        "install_plan": install_plan_payload(
            profile=install_profile,
            assets=assets,
            custom_nodes=custom_nodes,
            missing=missing_installable,
            base_dir=active_base_dir,
        ),
        "needs_install": bool(missing_installable),
        "repair_needed": repair_needed,
        "blocked": blocked,
        "install_note": f"一键安装将按 {install_profile} 档位下载缺失文件；安装后需要重启 ComfyUI，让模型列表重新加载。",
    }


@app.get("/api/diagnostics")
async def diagnostics() -> dict[str, Any]:
    health_data = health_payload()
    status_payload = await status()
    bootstrap_payload = await build_bootstrap_status()
    environment_payload = await environment()
    return {
        "ok": True,
        "diagnostic_version": 1,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "health": health_data,
        "status": status_payload,
        "bootstrap": bootstrap_payload,
        "prerequisites": bootstrap_payload.get("prerequisites"),
        "download_settings": download_settings_payload(),
        "recent_jobs": recent_jobs_payload(),
        "environment": {
            "base_dir": environment_payload.get("base_dir"),
            "configured_base_dir": environment_payload.get("configured_base_dir"),
            "workspace_dir": environment_payload.get("workspace_dir"),
            "paths": environment_payload.get("paths"),
            "os": environment_payload.get("os"),
            "comfy": environment_payload.get("comfy"),
            "hardware": environment_payload.get("hardware"),
            "install_profile": environment_payload.get("install_profile"),
            "recommended_install_profile": environment_payload.get("recommended_install_profile"),
            "install_profiles": environment_payload.get("install_profiles"),
            "install_plan": environment_payload.get("install_plan"),
            "download_settings": environment_payload.get("download_settings"),
            "missing_installable": environment_payload.get("missing_installable"),
            "diagnostics": environment_payload.get("diagnostics"),
            "tools": environment_payload.get("tools"),
            "blocked": environment_payload.get("blocked"),
        },
    }


async def build_bootstrap_status(request: Request | None = None) -> dict[str, Any]:
    try:
        system_stats = await comfy_get("/system_stats")
        return bootstrap_status_payload(True, paths=comfy_paths_from_system_stats(system_stats), request=request)
    except HTTPException as exc:
        return bootstrap_status_payload(False, str(exc.detail), request=request)


@app.get("/api/bootstrap")
async def bootstrap_status(request: Request) -> dict[str, Any]:
    return await build_bootstrap_status(request)


@app.get("/api/prerequisites")
async def prerequisites(network: bool = False) -> dict[str, Any]:
    if not BUILD_PREREQUISITE_REPORT:
        raise HTTPException(status_code=503, detail="前置条件检测模块不可用")
    return call_with_download_env(BUILD_PREREQUISITE_REPORT, WORKSPACE_DIR, BASE_DIR, COMFY_INSTALL_DIR, include_network=network)


@app.get("/api/settings")
async def get_settings() -> dict[str, Any]:
    return {"download": download_settings_payload(include_sensitive=True)}


@app.post("/api/settings")
async def save_settings(payload: dict[str, Any] = Body(default={})) -> dict[str, Any]:
    download = payload.get("download") if isinstance(payload.get("download"), dict) else payload
    try:
        hf_endpoint = normalize_hf_endpoint(str((download or {}).get("hf_endpoint") or ""))
        proxy_url = normalize_proxy_url(str((download or {}).get("proxy_url") or ""))
        pip_index_url = normalize_pip_index_url(str((download or {}).get("pip_index_url") or ""))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    config = read_local_config()
    if hf_endpoint:
        config["hf_endpoint"] = hf_endpoint
    else:
        config.pop("hf_endpoint", None)
    if proxy_url:
        config["proxy_url"] = proxy_url
    else:
        config.pop("proxy_url", None)
    if pip_index_url:
        config["pip_index_url"] = pip_index_url
    else:
        config.pop("pip_index_url", None)
    write_local_config(config)
    return {"ok": True, "download": download_settings_payload(include_sensitive=True)}


@app.post("/api/self-test")
async def start_self_test() -> dict[str, Any]:
    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "kind": "self_test",
        "status": "queued",
        "completed": False,
        "created_at": now_ms(),
        "log": [],
    }
    INSTALL_JOBS[job_id] = job
    thread = threading.Thread(target=run_self_test_worker, args=(job_id,), daemon=True)
    thread.start()
    return job


@app.post("/api/bootstrap/full-setup")
async def full_setup(backend: str = "auto", profile: str = "auto", dry_run: bool = False) -> dict[str, Any]:
    if backend not in {"auto", "cuda", "cpu", "mps", "skip"}:
        raise HTTPException(status_code=400, detail="未知 PyTorch 后端")
    normalized_profile = normalize_install_profile(profile)
    comfy_paths = await active_comfy_paths()
    active_base_dir = Path(comfy_paths["base_dir"])
    runtime = comfy_runtime_info()
    skip_comfy_install = comfy_paths.get("source") == "running_comfyui" or bool(runtime.get("source_ready"))
    running_main_py = Path(str(comfy_paths.get("main_py") or "")).resolve() if comfy_paths.get("main_py") else None
    local_main_py = Path(str(runtime.get("main_py") or COMFY_INSTALL_DIR / "main.py")).resolve()
    use_local_comfy_python = bool(runtime.get("source_ready")) or not skip_comfy_install or running_main_py == local_main_py
    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "kind": "full_setup",
        "status": "queued",
        "completed": False,
        "created_at": now_ms(),
        "backend": backend,
        "requested_profile": normalized_profile,
        "base_dir": str(active_base_dir),
        "paths": serializable_comfy_paths(comfy_paths),
        "dry_run": dry_run,
        "skip_comfy_install": skip_comfy_install,
        "use_local_comfy_python": use_local_comfy_python,
        "runtime_kind": runtime.get("kind"),
        "runtime_label": runtime.get("label"),
        "log": [
            "一键准备会按顺序安装/更新 ComfyUI、安装当前档位模型和节点，然后尝试启动 ComfyUI。",
            "模型文件很大，首次真实运行可能需要较长时间；dry-run 模式不会下载或写入。",
        ],
    }
    INSTALL_JOBS[job_id] = job
    thread = threading.Thread(
        target=run_full_setup_worker,
        args=(job_id,),
        kwargs={
            "backend": backend,
            "profile": normalized_profile,
            "base_dir": active_base_dir,
            "dry_run": dry_run,
            "skip_comfy_install": skip_comfy_install,
            "use_local_comfy_python": use_local_comfy_python,
        },
        daemon=True,
    )
    thread.start()
    return job


@app.post("/api/bootstrap/install-comfyui")
async def install_comfyui(backend: str = "auto") -> dict[str, Any]:
    if backend not in {"auto", "cuda", "cpu", "mps", "skip"}:
        raise HTTPException(status_code=400, detail="未知 PyTorch 后端")
    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "kind": "install_comfyui",
        "status": "queued",
        "completed": False,
        "created_at": now_ms(),
        "log": [],
    }
    INSTALL_JOBS[job_id] = job
    if COMFYUI_DISK_PLAN:
        disk_plan = COMFYUI_DISK_PLAN(COMFY_INSTALL_DIR, backend)
        job["install_disk"] = disk_plan
        if disk_plan.get("ok") is False:
            job["status"] = "failed"
            job["completed"] = True
            job["finished_at"] = now_ms()
            job["log"].append("ComfyUI 安装磁盘空间不足，已停止安装。")
            job["log"].append(
                f"当前剩余 {disk_plan.get('free_gb')} GB；"
                f"建议至少预留 {disk_plan.get('recommended_free_gb')} GB；"
                f"目标路径：{disk_plan.get('path')}"
            )
            attach_failure_hint(job)
            return job
    script = WORKSPACE_DIR / "scripts" / "install_comfyui.py"
    if not script.exists():
        job["status"] = "failed"
        job["completed"] = True
        job["finished_at"] = now_ms()
        job["log"].append(f"安装脚本不存在：{script}")
        attach_failure_hint(job)
        return job
    try:
        run_download_network_preflight(job, required_download_hosts_for_comfy_install(backend))
    except Exception as exc:
        job["status"] = "failed"
        job["completed"] = True
        job["finished_at"] = now_ms()
        job["log"].append(f"ComfyUI 安装前网络预检失败：{exc}")
        attach_failure_hint(job)
        return job
    command = [
        sys.executable,
        "-u",
        str(script),
        "--base-dir",
        str(BASE_DIR),
        "--install-dir",
        str(COMFY_INSTALL_DIR),
        "--backend",
        backend,
    ]
    job["command"] = install_command_text(command)
    thread = threading.Thread(target=run_install_worker, args=(job_id, command, download_subprocess_env()), daemon=True)
    thread.start()
    return job


@app.post("/api/bootstrap/start-comfyui")
async def start_comfyui() -> dict[str, Any]:
    global COMFY_PROCESS, COMFY_START_JOB_ID
    try:
        await comfy_get("/system_stats")
        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "kind": "start_comfyui",
            "status": "success",
            "completed": True,
            "created_at": now_ms(),
            "finished_at": now_ms(),
            "log": [f"ComfyUI 已在运行：{COMFY_URL}"],
        }
        INSTALL_JOBS[job_id] = job
        return job
    except HTTPException:
        pass

    runtime = comfy_runtime_info()
    main_py = Path(str(runtime["main_py"]))
    python = Path(str(runtime["python"]))
    if COMFY_PROCESS and COMFY_PROCESS.poll() is None:
        if COMFY_START_JOB_ID:
            existing_job = INSTALL_JOBS.get(COMFY_START_JOB_ID)
            if existing_job and not existing_job.get("completed"):
                return existing_job
        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "kind": "start_comfyui",
            "status": "running",
            "completed": False,
            "created_at": now_ms(),
            "log": ["ComfyUI 启动进程已经在运行，正在等待服务就绪。"],
        }
        INSTALL_JOBS[job_id] = job
        COMFY_START_JOB_ID = job_id
        thread = threading.Thread(target=wait_for_comfyui_ready_worker, args=(job_id,), daemon=True)
        thread.start()
        return job

    job_id = uuid.uuid4().hex
    if not runtime.get("source_ready"):
        desktop_exe = Path(str(runtime.get("desktop_exe") or ""))
        if not runtime.get("desktop_available") or not desktop_exe.exists():
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "ComfyUI 未安装或 Python 未就绪",
                    "errors": [
                        f"未找到可启动 main.py + Python：{main_py} / {python}",
                        "也未找到可启动的 ComfyUI Desktop 应用。",
                    ],
                    "actions": [
                        "点击“安装/更新 ComfyUI”安装源码版 ComfyUI。",
                        "或设置 COMFY_INSTALL_DIR 指向源码版 ComfyUI，设置 COMFY_DESKTOP_EXE 指向 Desktop 应用。",
                    ],
                },
            )
        job = {
            "id": job_id,
            "kind": "start_comfyui_desktop",
            "status": "queued",
            "completed": False,
            "created_at": now_ms(),
            "log": [],
            "runtime_kind": runtime.get("kind"),
        }
        INSTALL_JOBS[job_id] = job
        COMFY_START_JOB_ID = job_id
        launch_comfy_desktop_background(job_id, desktop_exe)
        return job

    command = comfy_start_command()
    job = {
        "id": job_id,
        "kind": "start_comfyui",
        "status": "queued",
        "completed": False,
        "created_at": now_ms(),
        "log": [],
        "command": install_command_text(command),
        "runtime_kind": runtime.get("kind"),
        "runtime_label": runtime.get("label"),
    }
    INSTALL_JOBS[job_id] = job
    COMFY_START_JOB_ID = job_id
    thread = threading.Thread(target=run_comfyui_worker, args=(job_id, command), daemon=True)
    thread.start()
    return job


@app.post("/api/install")
async def install_workflow_assets(profile: str = "auto") -> dict[str, Any]:
    comfy_paths = await active_comfy_paths()
    active_base_dir = Path(comfy_paths["base_dir"])
    assets = collect_asset_checks(active_base_dir)
    custom_nodes = collect_custom_node_checks(active_base_dir)
    normalized_profile = normalize_install_profile(profile)
    try:
        system_stats_for_profile = await comfy_get("/system_stats")
    except HTTPException:
        system_stats_for_profile = None
    hardware = build_hardware_summary(system_stats_for_profile)
    if normalized_profile == "auto":
        normalized_profile = install_profile_for_hardware(hardware)
    install_assets = filter_assets_for_install_profile(assets, normalized_profile)
    install_custom_nodes = filter_custom_nodes_for_install_profile(custom_nodes, normalized_profile)
    missing = installable_missing_items(install_assets, install_custom_nodes)
    object_info: dict[str, Any] | None = None
    try:
        object_info = await comfy_get("/object_info")
    except HTTPException:
        object_info = None
    diagnostics = build_load_diagnostics(
        paths=comfy_paths,
        assets=install_assets,
        custom_nodes=install_custom_nodes,
        node_checks=collect_node_checks(object_info),
        registry_checks=filter_registry_for_install_profile(collect_model_registry_checks(object_info), normalized_profile),
        object_info=object_info,
    )
    repair_needed = any(item.get("level") == "blocked" for item in diagnostics)
    install_plan = install_plan_payload(
        profile=normalized_profile,
        assets=assets,
        custom_nodes=custom_nodes,
        missing=missing,
        base_dir=active_base_dir,
    )
    disk_plan = install_plan.get("disk") or {}
    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "status": "queued",
        "completed": False,
        "created_at": now_ms(),
        "base_dir": str(active_base_dir),
        "paths": serializable_comfy_paths(comfy_paths),
        "install_profile": normalized_profile,
        "missing": missing,
        "install_plan": install_plan,
        "repair_needed": repair_needed,
        "download_settings": download_settings_payload(),
        "log": [],
    }
    INSTALL_JOBS[job_id] = job

    if not missing and not repair_needed:
        job["status"] = "success"
        job["completed"] = True
        job["finished_at"] = now_ms()
        job["skipped"] = True
        job["log"].append(f"安装目标目录：{active_base_dir}")
        job["log"].append("所有可一键安装的模型、权重和自定义节点都已存在，无需下载。")
        return job

    if missing and disk_plan.get("ok") is False:
        job["status"] = "failed"
        job["completed"] = True
        job["finished_at"] = now_ms()
        job["log"].append(f"安装目标目录：{active_base_dir}")
        job["log"].append("磁盘空间不足，已停止一键安装。")
        job["log"].append(
            f"当前剩余 {disk_plan.get('free_gb')} GB；"
            f"预计还需下载 {disk_plan.get('required_gb')} GB；"
            f"建议至少预留 {disk_plan.get('recommended_free_gb')} GB。"
        )
        job["log"].append("请清理磁盘空间，或把 COMFY_BASE_DIR 指向空间更大的磁盘后重新启动前端。")
        attach_failure_hint(job)
        return job

    script = WORKSPACE_DIR / "scripts" / "install_workflow_assets.py"
    if not script.exists():
        job["status"] = "failed"
        job["completed"] = True
        job["finished_at"] = now_ms()
        job["log"].append(f"安装脚本不存在：{script}")
        attach_failure_hint(job)
        return job

    python = comfy_venv_python()
    try:
        run_download_network_preflight(
            job,
            required_download_hosts_for_workflow_assets(
                profile=normalized_profile,
                base_dir=active_base_dir,
                use_local_comfy_python=python.exists(),
                repair_needed=repair_needed,
            ),
        )
    except Exception as exc:
        job["status"] = "failed"
        job["completed"] = True
        job["finished_at"] = now_ms()
        job["log"].append(f"模型/节点安装前网络预检失败：{exc}")
        attach_failure_hint(job)
        return job

    command = [
        sys.executable,
        "-u",
        str(script),
        "--base-dir",
        str(active_base_dir),
        "--profile",
        normalized_profile,
    ]
    if python.exists():
        command.extend(["--comfy-python", str(python)])
    job["command"] = install_command_text(command)
    if comfy_paths.get("base_dir_mismatch"):
        job["log"].append(f"检测到 ComfyUI 正在使用 {active_base_dir}，本次安装会写入该目录。")
        job["log"].append(f"前端默认目录是 {BASE_DIR}，如之前下载到默认目录，当前 ComfyUI 不会自动扫描。")
    if repair_needed and not missing:
        job["log"].append("未发现缺失文件，但发现已下载未加载的问题；将重新安装自定义节点依赖并校验文件。")
    thread = threading.Thread(target=run_install_worker, args=(job_id, command, download_subprocess_env()), daemon=True)
    thread.start()
    return job


@app.get("/api/install-plan")
async def install_plan(profile: str = "auto") -> dict[str, Any]:
    return await install_profile_plan_payload(profile)


@app.get("/api/install/{job_id}")
async def get_install_job(job_id: str) -> dict[str, Any]:
    job = INSTALL_JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="安装任务不存在")
    return job


@app.get("/api/validate")
async def validate() -> dict[str, Any]:
    comfy_paths = await active_comfy_paths()
    active_base_dir = Path(comfy_paths["base_dir"])
    try:
        system_stats = await comfy_get("/system_stats")
    except HTTPException:
        system_stats = None
    install_profile = install_profile_for_hardware(build_hardware_summary(system_stats))
    object_info = await comfy_get("/object_info")

    assets = filter_assets_for_install_profile(collect_asset_checks(active_base_dir), install_profile)
    custom_nodes = filter_custom_nodes_for_install_profile(collect_custom_node_checks(active_base_dir), install_profile)
    file_checks = [
        {
            "path": item.get("relative_path") or item.get("path"),
            "ok": bool(item.get("ok")),
            "size_gb": item.get("size_gb"),
        }
        for item in assets
    ] + [
        {
            "path": item.get("relative_path") or item.get("path"),
            "ok": bool(item.get("ok")),
            "size_gb": None,
        }
        for item in custom_nodes
    ]

    graph_checks: list[dict[str, Any]] = []
    sample_params = {
        "prompt": DEFAULT_PROMPT,
        "negative": DEFAULT_NEGATIVE,
        "image_name": DEFAULT_IMAGE,
        "width": 1280,
        "height": 704,
        "length": 81,
        "fps": 24,
        "seed": 1,
        "steps": 4,
        "cfg": 1.0,
    }
    graphs: dict[str, dict[str, Any]] = {}
    if install_profile == "cuda-full":
        graphs.update(
            {
                "I2V A14B": build_a14b_i2v_prompt(**sample_params),
                "T2V A14B": build_a14b_t2v_prompt(
                    **{k: v for k, v in sample_params.items() if k != "image_name"}
                ),
                "TI2V 5B": build_ti2v_prompt(**{**sample_params, "steps": 20, "cfg": 5.0}),
            }
        )
    if install_profile == "cuda-wan5b":
        graphs["TI2V 5B"] = build_ti2v_prompt(
            **{**sample_params, "width": 832, "height": 480, "length": 49, "steps": 20, "cfg": 5.0}
        )
    if install_profile in {"mac-low", "mac-balanced", "mac-wan5b"}:
        graphs.update(
            {
                "Mac LTX I2V": build_ltx_i2v_prompt(
                    **{**sample_params, "width": 512, "height": 320, "length": 25, "steps": 12, "cfg": 3.0}
                ),
                "Mac LTX T2V": build_ltx_t2v_prompt(
                    **{
                        k: v
                        for k, v in {
                            **sample_params,
                            "width": 512,
                            "height": 320,
                            "length": 25,
                            "steps": 12,
                            "cfg": 3.0,
                        }.items()
                        if k != "image_name"
                    }
                ),
            }
        )
    if install_profile == "mac-wan5b":
        graphs["Mac Wan5B TI2V"] = build_ti2v_prompt(**{**sample_params, "width": 832, "height": 480, "length": 49, "steps": 20, "cfg": 5.0})
    if install_profile in {"cuda-full", "cuda-wan5b", "mac-low", "mac-balanced", "mac-wan5b", "post-only"}:
        graphs.update(
            {
                "RIFE 2x": build_rife_prompt(video_name="sample.mp4", fps=24, multiplier=2),
                "Upscale 2x": build_video_upscale_prompt(video_name="sample.mp4", fps=24),
            }
        )
    required_nodes = sorted({class_type for graph in graphs.values() for class_type in graph_class_types(graph)})
    node_checks = [{"name": name, "ok": name in object_info} for name in required_nodes]
    for name, graph in graphs.items():
        errors = validate_graph(graph)
        graph_checks.append({"name": name, "ok": not errors, "errors": errors})

    tool_checks = ffmpeg_tool_checks()

    ok = all(item["ok"] for item in node_checks + file_checks + graph_checks) and all(
        item["ok"] for item in tool_checks if item.get("required", True)
    )
    return {
        "ok": ok,
        "paths": serializable_comfy_paths(comfy_paths),
        "nodes": node_checks,
        "files": file_checks,
        "graphs": graph_checks,
        "tools": tool_checks,
    }


@app.get("/api/workflow")
async def workflow_preview(
    mode: str = "",
    model_profile: str = "",
    model_label: str = "",
    width: int = 1280,
    height: int = 704,
    length: int = 81,
    fps: int = 24,
    seed: str = "1",
    steps: int = 4,
    cfg: float = 1.0,
    upscale_model: str = "RealESRGAN_x2plus.pth",
    rife_multiplier: int = 2,
    has_image: bool = False,
    include_graph: bool = False,
) -> dict[str, Any]:
    width = normalize_dimension(width)
    height = normalize_dimension(height)
    length = normalize_frames(length)
    fps = clamp_int(fps, 1, 60)
    seed_value = normalize_seed(seed)
    steps = clamp_int(steps, 1, 80)
    cfg = clamp_float(cfg, 0.1, 20.0)
    upscale_model = upscale_model if upscale_model in {"RealESRGAN_x2plus.pth", "4x-UltraSharp.pth"} else "RealESRGAN_x2plus.pth"
    rife_multiplier = clamp_int(rife_multiplier, 2, 4)
    resolved = resolve_workflow_profile(
        model_profile=model_profile,
        mode=mode,
        model_label=model_label,
        upscale_model=upscale_model,
        rife_multiplier=rife_multiplier,
    )
    mode = resolved["mode"]
    upscale_model = resolved["upscale_model"]
    rife_multiplier = resolved["rife_multiplier"]
    if mode in {"ltx_i2v", "ltx_t2v"}:
        length = normalize_ltx_frames(length)

    if mode == "":
        return {
            "ok": True,
            "mode": mode,
            "profile": resolved["profile"],
            "label": resolved["label"],
            "local": False,
            "node_count": 0,
            "class_types": [],
            "output_nodes": [],
            "errors": [],
            "message": "当前步骤不需要创建 ComfyUI 视频工作流。",
        }
    if mode == "unsupported":
        return {
            "ok": False,
            "mode": mode,
            "profile": resolved["profile"],
            "label": resolved["label"] or "当前硬件不建议本地生视频",
            "local": False,
            "node_count": 0,
            "class_types": [],
            "output_nodes": [],
            "errors": ["当前环境属于后期处理路线：没有检测到适合本地视频生成的 CUDA 或 Apple Silicon MPS 后端。"],
            "message": "当前硬件不建议本地生视频。",
        }

    if mode == "deflicker":
        ffmpeg_tools = ffmpeg_tool_checks()
        ffmpeg_probe_item = ffmpeg_tools[0]
        filters_ok = all(item.get("ok") for item in ffmpeg_tools)
        errors = [item.get("message") or item["name"] for item in ffmpeg_tools if not item.get("ok")]
        return {
            "ok": bool(filters_ok),
            "mode": mode,
            "profile": resolved["profile"],
            "label": resolved["label"] or "ffmpeg deflicker + hqdn3d",
            "local": True,
            "node_count": 0,
            "class_types": [],
            "output_nodes": [],
            "errors": [] if filters_ok else errors,
            "tool": {"ffmpeg": ffmpeg_probe_item.get("path", ""), "source": ffmpeg_probe_item.get("source", ""), "filters_ok": filters_ok},
            "message": "本步骤使用本地 ffmpeg，不需要 ComfyUI 图。" if filters_ok else "本地 ffmpeg 预检未通过。",
        }

    graph = build_preview_workflow_graph(
        mode=mode,
        prompt=text_or_default("", DEFAULT_PROMPT),
        negative=text_or_default("", DEFAULT_NEGATIVE),
        width=width,
        height=height,
        length=length,
        fps=fps,
        seed=seed_value,
        steps=steps,
        cfg=cfg,
        upscale_model=upscale_model,
        rife_multiplier=rife_multiplier,
    )
    if graph is None:
        raise HTTPException(status_code=400, detail="未知模式，无法创建 ComfyUI 工作流")

    comfy_paths = await active_comfy_paths()
    object_info: dict[str, Any] | None = None
    object_error = ""
    try:
        object_info = await comfy_get("/object_info")
        graph = resolve_graph_model_option_names(graph, object_info)
    except HTTPException as exc:
        object_error = str(exc.detail)
    graph_errors = validate_graph(graph)
    availability = await comfy_node_availability(graph)
    model_errors = graph_model_registry_errors(graph, object_info)
    try:
        system_stats = await comfy_get("/system_stats")
    except HTTPException:
        system_stats = None
    risk_checks = workflow_risk_checks(
        mode=mode,
        hardware=build_hardware_summary(system_stats),
        width=width,
        height=height,
        length=length,
    )
    output_nodes = [
        node_id
        for node_id, item in graph.items()
        if item.get("class_type") in {"SaveVideo", "VHS_VideoCombine", "SaveImage"}
    ]
    errors = list(graph_errors)
    errors.extend(default_keyframe_errors(mode=mode, has_upload=has_image, paths=comfy_paths))
    errors.extend(model_errors)
    errors.extend(item["message"] for item in risk_checks if item.get("level") == "blocked")
    if availability["missing"]:
        errors.append(f"ComfyUI 缺少节点：{', '.join(availability['missing'])}")
    if availability["error"]:
        errors.append(availability["error"])
    if object_error and object_error not in errors:
        errors.append(object_error)
    response: dict[str, Any] = {
        "ok": not errors,
        "mode": mode,
        "profile": resolved["profile"],
        "label": resolved["label"],
        "local": False,
        "node_count": len(graph),
        "class_types": graph_class_types(graph),
        "output_nodes": output_nodes,
        "errors": errors,
        "risk_checks": risk_checks,
        "comfy": availability,
        "parameters": {
            "width": width,
            "height": height,
            "length": length,
            "fps": fps,
            "steps": steps,
            "cfg": cfg,
            "upscale_model": upscale_model,
            "rife_multiplier": rife_multiplier,
        },
        "message": "ComfyUI workflow 已创建并预检通过。" if not errors else "ComfyUI workflow 预检未通过。",
    }
    if include_graph:
        response["graph"] = graph
    return response


@app.post("/api/video-smoke-test")
async def video_smoke_test() -> dict[str, Any]:
    try:
        system_stats = await comfy_get("/system_stats")
        object_info = await comfy_get("/object_info")
    except HTTPException as exc:
        raise HTTPException(status_code=503, detail=comfy_not_connected_detail("运行生成链路测试", exc.detail)) from exc

    comfy_paths = comfy_paths_from_system_stats(system_stats)
    active_base_dir = Path(comfy_paths["base_dir"])
    os_info = {
        "name": platform.system(),
        "release": platform.release(),
        "version": platform.version(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "python": sys.version.split()[0],
        "python_executable": sys.executable,
    }
    hardware = build_hardware_summary(system_stats)
    assets = collect_asset_checks(active_base_dir)
    custom_nodes = collect_custom_node_checks(active_base_dir)
    tool_checks = ffmpeg_tool_checks()
    node_checks = collect_node_checks(object_info)
    model_options = workflow_model_options(
        os_info=os_info,
        hardware=hardware,
        assets=assets,
        custom_nodes=custom_nodes,
        tool_checks=tool_checks,
        node_checks=node_checks,
        base_dir=active_base_dir,
    )
    option = choose_video_smoke_option(model_options)
    if not option:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "没有可用于生成链路测试的视频模型",
                "errors": [
                    "请先在环境侦测里安装推荐档位，并确认 ComfyUI 已重启加载节点和模型。",
                    "无 CUDA/MPS 的后期路线不会运行本地视频生成测试。",
                ],
            },
        )

    mode = str(option["mode"])
    keyframe_errors = default_keyframe_errors(mode=mode, has_upload=False, paths=comfy_paths)
    if keyframe_errors:
        raise HTTPException(status_code=400, detail={"message": "示例关键帧不可用", "errors": keyframe_errors})

    params = video_smoke_parameters(option)
    graph = build_preview_workflow_graph(
        mode=mode,
        prompt=DEFAULT_PROMPT + ", short smoke test clip, stable camera, no scene cuts",
        negative=DEFAULT_NEGATIVE,
        width=params["width"],
        height=params["height"],
        length=params["length"],
        fps=params["fps"],
        seed=1,
        steps=params["steps"],
        cfg=params["cfg"],
        upscale_model="RealESRGAN_x2plus.pth",
        rife_multiplier=2,
    )
    if graph is None:
        raise HTTPException(status_code=400, detail="无法创建生成链路测试 workflow")

    graph = resolve_graph_model_option_names(graph, object_info)
    graph_errors = validate_graph(graph)
    graph_errors.extend(graph_model_registry_errors(graph, object_info))
    missing_nodes = [name for name in graph_class_types(graph) if name not in object_info]
    if missing_nodes:
        graph_errors.append(f"ComfyUI 缺少节点：{', '.join(missing_nodes)}")
    graph_errors.extend(
        item["message"]
        for item in workflow_risk_checks(mode=mode, hardware=hardware, width=params["width"], height=params["height"], length=params["length"])
        if item.get("level") == "blocked"
    )
    if graph_errors:
        raise HTTPException(status_code=400, detail={"message": "生成链路测试预检未通过", "errors": graph_errors})

    response = await comfy_post("/prompt", {"prompt": graph, "client_id": CLIENT_ID})
    prompt_id = response.get("prompt_id")
    if not prompt_id:
        raise HTTPException(status_code=500, detail=response)

    job_id = uuid.uuid4().hex
    JOBS[job_id] = {
        "id": job_id,
        "prompt_id": prompt_id,
        "kind": "video_smoke_test",
        "mode": mode,
        "title": f"生成链路测试 - {option.get('label') or option.get('model_label') or mode}",
        "status": "queued",
        "created_at": now_ms(),
        "seed": 1,
        "width": params["width"],
        "height": params["height"],
        "length": params["length"],
        "fps": params["fps"],
        "media": [],
        "raw": response,
        "model_profile": option.get("id"),
        "smoke_test": True,
        "parameters": params,
    }
    return JOBS[job_id]


@app.post("/api/generate")
async def generate(
    mode: str = Form(...),
    prompt: str = Form(""),
    negative: str = Form(""),
    width: int = Form(1280),
    height: int = Form(704),
    length: int = Form(81),
    fps: int = Form(24),
    seed: str = Form("-1"),
    steps: int = Form(4),
    cfg: float = Form(1.0),
    model_label: str = Form(""),
    model_profile: str = Form(""),
    upscale_model: str = Form("RealESRGAN_x2plus.pth"),
    rife_multiplier: int = Form(2),
    source_video_filename: str = Form(""),
    source_video_subfolder: str = Form(""),
    source_video_type: str = Form(""),
    image: UploadFile | None = File(None),
    video: UploadFile | None = File(None),
) -> JSONResponse:
    prompt_text = text_or_default(prompt, DEFAULT_PROMPT)
    negative_text = text_or_default(negative, DEFAULT_NEGATIVE)
    width = normalize_dimension(width)
    height = normalize_dimension(height)
    length = normalize_frames(length)
    fps = clamp_int(fps, 1, 60)
    seed_value = normalize_seed(seed)
    steps = clamp_int(steps, 1, 80)
    cfg = clamp_float(cfg, 0.1, 20.0)
    selected_label = clean_label(model_label)
    upscale_model = upscale_model if upscale_model in {"RealESRGAN_x2plus.pth", "4x-UltraSharp.pth"} else "RealESRGAN_x2plus.pth"
    rife_multiplier = clamp_int(rife_multiplier, 2, 4)
    resolved = resolve_workflow_profile(
        model_profile=model_profile,
        mode=mode,
        model_label=selected_label,
        upscale_model=upscale_model,
        rife_multiplier=rife_multiplier,
    )
    comfy_paths = await active_comfy_paths()
    mode = resolved["mode"]
    selected_label = resolved["label"]
    upscale_model = resolved["upscale_model"]
    rife_multiplier = clamp_int(resolved["rife_multiplier"], 2, 4)
    if mode in {"ltx_i2v", "ltx_t2v"}:
        length = normalize_ltx_frames(length)
    if mode == "unsupported":
        raise HTTPException(
            status_code=400,
            detail={
                "message": "当前硬件不建议本地生视频",
                "errors": ["可以上传已有视频继续做去闪烁、插帧和清晰度增强，或换 CUDA / Apple Silicon MPS 环境后再生成。"],
            },
        )

    if mode in {"i2v_a14b", "ti2v_5b", "ltx_i2v"}:
        keyframe_errors = default_keyframe_errors(
            mode=mode,
            has_upload=bool(image and image.filename),
            paths=comfy_paths,
        )
        if keyframe_errors:
            raise HTTPException(status_code=400, detail={"message": "关键帧不可用", "errors": keyframe_errors})
        image_name = DEFAULT_IMAGE
        if image and image.filename:
            image_name = await save_upload(image, IMAGE_EXTENSIONS, comfy_paths)
    else:
        image_name = ""

    if mode == "i2v_a14b":
        graph = build_a14b_i2v_prompt(
            prompt=prompt_text,
            negative=negative_text,
            image_name=image_name,
            width=width,
            height=height,
            length=length,
            fps=fps,
            seed=seed_value,
            steps=steps,
            cfg=cfg,
        )
        title = selected_label or "I2V A14B 正片"
    elif mode == "t2v_a14b":
        graph = build_a14b_t2v_prompt(
            prompt=prompt_text,
            negative=negative_text,
            width=width,
            height=height,
            length=length,
            fps=fps,
            seed=seed_value,
            steps=steps,
            cfg=cfg,
        )
        title = selected_label or "T2V A14B 文生视频"
    elif mode == "ti2v_5b":
        graph = build_ti2v_prompt(
            prompt=prompt_text,
            negative=negative_text,
            image_name=image_name,
            width=width,
            height=height,
            length=length,
            fps=fps,
            seed=seed_value,
            steps=steps,
            cfg=cfg,
        )
        title = selected_label or "TI2V 5B 草稿"
    elif mode == "ltx_i2v":
        graph = build_ltx_i2v_prompt(
            prompt=prompt_text,
            negative=negative_text,
            image_name=image_name,
            width=width,
            height=height,
            length=length,
            fps=fps,
            seed=seed_value,
            steps=steps,
            cfg=cfg,
        )
        title = selected_label or "Mac LTX I2V"
    elif mode == "ltx_t2v":
        graph = build_ltx_t2v_prompt(
            prompt=prompt_text,
            negative=negative_text,
            width=width,
            height=height,
            length=length,
            fps=fps,
            seed=seed_value,
            steps=steps,
            cfg=cfg,
        )
        title = selected_label or "Mac LTX T2V"
    elif mode == "rife_2x":
        if source_video_filename:
            video_name = copy_media_to_input(
                source_video_filename,
                source_video_subfolder,
                source_video_type or "output",
                comfy_paths,
            )
        elif video and video.filename:
            video_name = await save_upload(video, VIDEO_EXTENSIONS, comfy_paths)
        else:
            raise HTTPException(status_code=400, detail="RIFE 模式需要上传视频，或先完成上一阶段视频生成")
        graph = build_rife_prompt(video_name=video_name, fps=fps, multiplier=rife_multiplier)
        title = selected_label or f"RIFE {rife_multiplier}x 插帧"
    elif mode == "deflicker":
        if source_video_filename:
            source_path = safe_media_path(
                source_video_filename,
                source_video_subfolder,
                source_video_type or "output",
                comfy_paths,
            )
        elif video and video.filename:
            video_name = await save_upload(video, VIDEO_EXTENSIONS, comfy_paths)
            source_path = input_media_path(video_name, comfy_paths)
        else:
            raise HTTPException(status_code=400, detail="闪烁修复需要上传视频，或先完成上一阶段视频生成")

        media = run_deflicker(source_path, comfy_paths)
        job_id = uuid.uuid4().hex
        JOBS[job_id] = {
            "id": job_id,
            "prompt_id": None,
            "mode": mode,
            "title": selected_label or "画面闪烁修复",
            "status": "success",
            "completed": True,
            "local": True,
            "created_at": now_ms(),
            "finished_at": now_ms(),
            "seed": seed_value,
            "width": width,
            "height": height,
            "length": length,
            "fps": fps,
            "media": [media],
            "raw": {"processor": "ffmpeg", "filters": "deflicker,hqdn3d"},
            "model_profile": resolved["profile"],
        }
        return JSONResponse(JOBS[job_id])
    elif mode == "upscale_2x":
        if source_video_filename:
            video_name = copy_media_to_input(
                source_video_filename,
                source_video_subfolder,
                source_video_type or "output",
                comfy_paths,
            )
        elif video and video.filename:
            video_name = await save_upload(video, VIDEO_EXTENSIONS, comfy_paths)
        else:
            raise HTTPException(status_code=400, detail="清晰度增强需要上传视频，或先完成上一阶段视频生成")
        graph = build_video_upscale_prompt(video_name=video_name, fps=fps, model_name=upscale_model)
        title = selected_label or (
            "4x-UltraSharp 清晰度增强"
            if upscale_model == "4x-UltraSharp.pth"
            else "RealESRGAN 2x 清晰度增强"
        )
    else:
        raise HTTPException(status_code=400, detail="未知模式")

    try:
        object_info = await comfy_get("/object_info")
    except HTTPException as exc:
        raise HTTPException(status_code=503, detail=comfy_not_connected_detail("提交 workflow", exc.detail)) from exc
    graph = resolve_graph_model_option_names(graph, object_info)
    graph_errors = validate_graph(graph)
    graph_errors.extend(graph_model_registry_errors(graph, object_info))
    missing_nodes = [name for name in graph_class_types(graph) if name not in object_info]
    if missing_nodes:
        graph_errors.append(f"ComfyUI 缺少节点：{', '.join(missing_nodes)}")
    try:
        system_stats = await comfy_get("/system_stats")
    except HTTPException:
        system_stats = None
    graph_errors.extend(
        item["message"]
        for item in workflow_risk_checks(
            mode=mode,
            hardware=build_hardware_summary(system_stats),
            width=width,
            height=height,
            length=length,
        )
        if item.get("level") == "blocked"
    )
    if graph_errors:
        raise HTTPException(status_code=400, detail={"message": "工作流图无效", "errors": graph_errors})

    response = await comfy_post("/prompt", {"prompt": graph, "client_id": CLIENT_ID})
    prompt_id = response.get("prompt_id")
    if not prompt_id:
        raise HTTPException(status_code=500, detail=response)

    job_id = uuid.uuid4().hex
    JOBS[job_id] = {
        "id": job_id,
        "prompt_id": prompt_id,
        "mode": mode,
        "title": title,
        "status": "queued",
        "created_at": now_ms(),
        "seed": seed_value,
        "width": width,
        "height": height,
        "length": length,
        "fps": fps,
        "media": [],
        "raw": response,
        "model_profile": resolved["profile"],
    }
    return JSONResponse(JOBS[job_id])


JOB_LOST_AFTER_MS = 120_000


def normalize_comfy_history_status(status_info: dict[str, Any]) -> tuple[str, bool]:
    raw_status = str(status_info.get("status_str") or "").strip().lower()
    completed = bool(status_info.get("completed"))
    if raw_status == "success":
        return "success", True
    if raw_status in {"error", "failed", "failure"}:
        return "failed", True
    if completed:
        return "failed", True
    return raw_status or "running", False


def friendly_comfy_messages(messages: Any) -> list[str]:
    if not isinstance(messages, list):
        return []
    friendly: list[str] = []
    for item in messages[-8:]:
        if isinstance(item, str):
            friendly.append(item)
        elif isinstance(item, (list, tuple)) and item:
            friendly.append(" ".join(str(part) for part in item if part is not None))
        elif isinstance(item, dict):
            friendly.append(json.dumps(item, ensure_ascii=False))
    return friendly


@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    if job.get("local"):
        return job

    history = await comfy_get(f"/history/{job['prompt_id']}")
    history_item = history.get(job["prompt_id"])
    if history_item:
        status_info = history_item.get("status", {})
        job["status"], job["completed"] = normalize_comfy_history_status(status_info)
        job["media"] = extract_media(history_item)
        job["messages"] = status_info.get("messages", [])
        job["friendly_messages"] = friendly_comfy_messages(job["messages"])
        if job["status"] == "failed" and not job.get("error"):
            job["error"] = "ComfyUI 执行失败。请查看下方节点日志，通常是缺模型、缺节点、显存不足或 dtype 不兼容。"
        if job["completed"]:
            job["finished_at"] = job.get("finished_at") or now_ms()
    else:
        queue = await comfy_get("/queue")
        queue_items = (queue.get("queue_running") or []) + (queue.get("queue_pending") or [])
        prompt_ids = {str(item[1]) for item in queue_items if isinstance(item, list) and len(item) > 1}
        if job["prompt_id"] in prompt_ids:
            job["status"] = "running"
            job.pop("missing_seen_at", None)
        else:
            missing_seen_at = int(job.setdefault("missing_seen_at", now_ms()))
            if now_ms() - missing_seen_at > JOB_LOST_AFTER_MS:
                job["status"] = "lost"
                job["completed"] = True
                job["finished_at"] = job.get("finished_at") or now_ms()
                job["error"] = "ComfyUI 队列和历史里都找不到这个任务，可能是 ComfyUI 重启、任务被清队或旧前端提交到了另一个实例。"
            else:
                job["status"] = "queued"

    return job


@app.get("/api/view")
async def view(filename: str, subfolder: str = "", type: str = "output") -> FileResponse:
    path = safe_media_path(filename, subfolder, type, await active_comfy_paths())
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=path.name)


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Any, exc: HTTPException) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})
