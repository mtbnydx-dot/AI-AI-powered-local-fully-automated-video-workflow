const defaultPrompt =
  "a realistic modern esports training room, Queensland esports branding style, soft RGB lighting, students using gaming peripherals, clean commercial photography, wide angle lens, natural shadows, cinematic slow dolly-in camera movement, polished commercial video";

const defaultNegative =
  "overexposed, low quality, blurry, jpeg artifacts, distorted hands, deformed face, extra fingers, warped screens, unreadable text, watermark, subtitles, flicker, jitter, chaotic background, NSFW";

const nativeFetch = window.fetch.bind(window);
const serviceModeLabels = {
  both: "本机一体",
  server: "服务端",
  client: "客户端",
};

let serviceConfig = {
  mode: "both",
  mode_configured: false,
  first_run_required: true,
  server_url: "",
  api_base_url: "",
  access_token: "",
};

function cleanBaseUrl(value) {
  return String(value || "").trim().replace(/\/+$/, "");
}

function localApiPath(value) {
  const text = String(value || "");
  if (text.startsWith("/api/") || text === "/api") return true;
  try {
    const parsed = new URL(text, window.location.href);
    return parsed.origin === window.location.origin && (parsed.pathname.startsWith("/api/") || parsed.pathname === "/api");
  } catch (_) {
    return false;
  }
}

function rewriteApiUrl(value) {
  const base = cleanBaseUrl(serviceConfig?.api_base_url || serviceConfig?.server_url || "");
  if (!base || serviceConfig?.mode !== "client" || !localApiPath(value)) return value;
  const parsed = new URL(String(value), window.location.href);
  return `${base}${parsed.pathname}${parsed.search}${parsed.hash}`;
}

function isApiUrl(value) {
  const text = String(value || "");
  if (text.startsWith("/api/") || text === "/api") return true;
  try {
    return new URL(text, window.location.href).pathname.startsWith("/api/");
  } catch (_) {
    return false;
  }
}

function buildServiceFetchInput(input) {
  const sourceUrl =
    typeof input === "string"
      ? input
      : input instanceof URL
        ? input.href
        : input instanceof Request
          ? input.url
          : "";
  const rewritten = rewriteApiUrl(sourceUrl);
  if (!sourceUrl || rewritten === sourceUrl) return input;
  if (input instanceof Request) return new Request(rewritten, input);
  if (input instanceof URL) return new URL(rewritten);
  return rewritten;
}

window.fetch = (input, init = {}) => {
  const sourceUrl =
    typeof input === "string"
      ? input
      : input instanceof URL
        ? input.href
        : input instanceof Request
          ? input.url
          : "";
  const rewritten = rewriteApiUrl(sourceUrl);
  const nextInput = buildServiceFetchInput(input);
  const shouldAttachToken = isApiUrl(sourceUrl) || isApiUrl(rewritten);
  if (!shouldAttachToken || !serviceConfig?.access_token) {
    return nativeFetch(nextInput, init);
  }
  const headers = new Headers(init.headers || (input instanceof Request ? input.headers : undefined));
  if (!headers.has("X-WAN22-Token") && !headers.has("Authorization")) {
    headers.set("X-WAN22-Token", serviceConfig.access_token);
  }
  return nativeFetch(nextInput, { ...init, headers });
};

function apiUrl(path) {
  return rewriteApiUrl(path);
}

function mediaUrl(path) {
  if (!path) return "";
  const text = String(path);
  if (/^https?:\/\//i.test(text) || text.startsWith("blob:") || text.startsWith("data:")) return text;
  return apiUrl(text);
}

const steps = [
  {
    id: "environment",
    mode: "",
    title: "1. 环境侦测",
    subtitle: "先确认这台机器适合跑哪一档视频模型。",
    badge: "体检",
    goal:
      "这一关会自动检测操作系统、GPU/显存、ComfyUI、ffmpeg、自定义节点和模型文件，并给出每一步推荐模型。",
    sections: ["environment"],
    primary: "进入关键帧",
  },
  {
    id: "keyframe",
    mode: "",
    title: "2. 关键帧",
    subtitle: "先固定画面，再让视频模型负责运动。",
    badge: "准备",
    goal:
      "这一关只做一件事：确定首帧。人物、空间、品牌风格、构图在这里定死，后面的视频会更稳。",
    sections: ["summary", "modelChoice", "prompt", "negative", "image", "keyframePreview"],
    primary: "确认关键帧，进入试镜头",
  },
  {
    id: "draft",
    mode: "ti2v_5b",
    title: "3. TI2V-5B 试镜头",
    subtitle: "先快速看动作、镜头运动和 Prompt 是否靠谱。",
    badge: "草稿",
    goal:
      "这一关用 5B 模型快速试镜头。草稿不追求最终画质，主要判断运动方向、速度、镜头是否符合预期。",
    sections: [
      "summary",
      "modelChoice",
      "prompt",
      "negative",
      "image",
      "keyframePreview",
      "size",
      "duration",
      "fps",
      "steps",
      "cfg",
      "seed",
    ],
    primary: "生成草稿",
    defaults: { mode: "ti2v_5b", steps: 20, cfg: 5.0 },
  },
  {
    id: "final",
    mode: "i2v_a14b",
    title: "4. A14B 正式片段",
    subtitle: "草稿方向确认后，再用大模型出 720P 正片。",
    badge: "正片",
    goal:
      "这一关使用 Wan2.2 I2V-A14B。推荐保留 3 到 5 秒短镜头，稳定性和可剪辑性最好。",
    sections: [
      "summary",
      "modelChoice",
      "prompt",
      "negative",
      "image",
      "keyframePreview",
      "size",
      "duration",
      "fps",
      "steps",
      "cfg",
      "seed",
    ],
    primary: "生成正片",
    defaults: { mode: "i2v_a14b", steps: 4, cfg: 1.0 },
  },
  {
    id: "deflicker",
    mode: "deflicker",
    title: "5. 画面闪烁修复",
    subtitle: "先稳定曝光和轻微纹理跳动，再进入插帧后期。",
    badge: "修复",
    goal:
      "这一关优先使用 A14B 正片。它会降低亮度闪烁、曝光跳动和轻微细节噪声，但不能修掉人物变形或物体乱变。",
    sections: ["summary", "modelChoice", "video"],
    primary: "开始闪烁修复",
    defaults: { mode: "deflicker" },
  },
  {
    id: "rife",
    mode: "rife_2x",
    title: "6. RIFE 2x 插帧",
    subtitle: "把最终片段插到更高帧率，运动更顺。",
    badge: "后期",
    goal:
      "这一关优先使用闪烁修复后的视频；如果没做修复，会使用 A14B 正片。RIFE 主要改善运动顺滑度。",
    sections: ["summary", "modelChoice", "video", "fps"],
    primary: "开始插帧",
    defaults: { mode: "rife_2x", fps: 24 },
  },
  {
    id: "upscale",
    mode: "upscale_2x",
    title: "7. 清晰度增强",
    subtitle: "用 2x 视频超分扩展分辨率，增加边缘和纹理清晰度。",
    badge: "超分",
    goal:
      "这一关优先使用插帧后的视频；如果没做插帧，会使用 A14B 正片。2x 超分会显著增加分辨率和文件大小，适合最终交付前处理。",
    sections: ["summary", "modelChoice", "video", "targetResolution", "fps"],
    primary: "开始清晰度增强",
    defaults: { mode: "upscale_2x", fps: 48 },
  },
  {
    id: "edit",
    mode: "",
    title: "8. 多镜头拼接",
    subtitle: "把多个 3 到 6 秒片段放进剪辑软件。",
    badge: "交付",
    goal:
      "这一关不再跑模型。把输出目录里的片段导入剪映、达芬奇或 PR，按分镜顺序拼接、调色、加字幕和音乐。",
    sections: ["summary"],
    primary: "完成",
  },
];

const fallbackModelOptions = {
  keyframe: [
    {
      id: "upload_keyframe",
      label: "上传/外部关键帧",
      mode: "",
      status: "ok",
      reason: "任何硬件都可用，先把构图定死。",
      defaults: {},
      uses_image: true,
      supported: true,
      model_label: "上传/外部关键帧",
    },
  ],
  draft: [
    {
      id: "wan22_ti2v_5b_720p",
      label: "Wan2.2 TI2V-5B 720P",
      mode: "ti2v_5b",
      status: "ok",
      reason: "推荐草稿档。",
      defaults: { width: 1280, height: 704, length: 81, fps: 24, steps: 20, cfg: 5.0 },
      uses_image: true,
      supported: true,
      model_label: "Wan2.2 TI2V-5B 720P",
    },
    {
      id: "wan22_ti2v_5b_480p",
      label: "Wan2.2 TI2V-5B 480P 小显存",
      mode: "ti2v_5b",
      status: "warn",
      reason: "低显存保守档。",
      defaults: { width: 832, height: 480, length: 49, fps: 24, steps: 18, cfg: 5.0 },
      uses_image: true,
      supported: true,
      model_label: "Wan2.2 TI2V-5B 480P 小显存",
    },
  ],
  final: [
    {
      id: "wan22_i2v_a14b_720p",
      label: "Wan2.2 I2V-A14B 720P",
      mode: "i2v_a14b",
      status: "ok",
      reason: "大显存正式出片首选。",
      defaults: { width: 1280, height: 704, length: 81, fps: 24, steps: 4, cfg: 1.0 },
      uses_image: true,
      supported: true,
      model_label: "Wan2.2 I2V-A14B 720P",
    },
    {
      id: "wan22_i2v_a14b_480p",
      label: "Wan2.2 I2V-A14B 480P",
      mode: "i2v_a14b",
      status: "warn",
      reason: "48GB 左右更现实的 A14B 档。",
      defaults: { width: 832, height: 480, length: 49, fps: 24, steps: 4, cfg: 1.0 },
      uses_image: true,
      supported: true,
      model_label: "Wan2.2 I2V-A14B 480P",
    },
    {
      id: "wan22_ti2v_5b_final_480p",
      label: "Wan2.2 TI2V-5B 480P 小显存正片",
      mode: "ti2v_5b",
      status: "warn",
      reason: "低配保底生成档，画质不如 A14B。",
      defaults: { width: 832, height: 480, length: 49, fps: 24, steps: 22, cfg: 5.0 },
      uses_image: true,
      supported: true,
      model_label: "Wan2.2 TI2V-5B 480P 小显存正片",
    },
  ],
  deflicker: [
    {
      id: "ffmpeg_deflicker_balanced",
      label: "ffmpeg deflicker + hqdn3d",
      mode: "deflicker",
      status: "ok",
      reason: "修曝光闪烁和轻微纹理跳动。",
      defaults: {},
      uses_image: false,
      supported: true,
      model_label: "ffmpeg deflicker + hqdn3d",
    },
  ],
  rife: [
    {
      id: "rife49_2x",
      label: "RIFE 4.9 2x",
      mode: "rife_2x",
      status: "ok",
      reason: "24fps 到 48fps。",
      defaults: { fps: 24 },
      uses_image: false,
      supported: true,
      rife_multiplier: 2,
      model_label: "RIFE 4.9 2x",
    },
  ],
  upscale: [
    {
      id: "realesrgan_x2plus",
      label: "RealESRGAN x2plus 2x",
      mode: "upscale_2x",
      status: "ok",
      reason: "默认 2x 超分。",
      defaults: { fps: 48 },
      uses_image: false,
      supported: true,
      scale: 2,
      upscale_model: "RealESRGAN_x2plus.pth",
      model_label: "RealESRGAN x2plus 2x",
    },
  ],
};

const fallbackRecommendedModels = {
  keyframe: "upload_keyframe",
  draft: "wan22_ti2v_5b_720p",
  final: "wan22_i2v_a14b_720p",
  deflicker: "ffmpeg_deflicker_balanced",
  rife: "rife49_2x",
  upscale: "realesrgan_x2plus",
};

const macFallbackModelOptions = {
  ...fallbackModelOptions,
  draft: [
    {
      id: "mac_ltx_low_i2v",
      label: "Mac LTX I2V 低档",
      mode: "ltx_i2v",
      status: "warn",
      reason: "Apple Silicon 首次跑通档；短镜头、小分辨率，优先稳定。",
      defaults: { width: 512, height: 320, length: 25, fps: 24, steps: 12, cfg: 3.0 },
      uses_image: true,
      supported: true,
      model_label: "Mac LTX I2V 低档",
    },
    {
      id: "mac_ltx_balanced_i2v",
      label: "Mac LTX I2V 均衡档",
      mode: "ltx_i2v",
      status: "warn",
      reason: "16GB 到 36GB Apple Silicon 的默认路线。",
      defaults: { width: 576, height: 320, length: 49, fps: 24, steps: 16, cfg: 3.0 },
      uses_image: true,
      supported: true,
      model_label: "Mac LTX I2V 均衡档",
    },
  ],
  final: [
    {
      id: "mac_ltx_low_i2v",
      label: "Mac LTX I2V 低档",
      mode: "ltx_i2v",
      status: "warn",
      reason: "Mac 保守正式片段档，适合先跑通再提高参数。",
      defaults: { width: 512, height: 320, length: 25, fps: 24, steps: 12, cfg: 3.0 },
      uses_image: true,
      supported: true,
      model_label: "Mac LTX I2V 低档",
    },
    {
      id: "mac_ltx_quality_i2v",
      label: "Mac LTX I2V 质量档",
      mode: "ltx_i2v",
      status: "warn",
      reason: "高内存 Apple Silicon 可尝试，仍建议短镜头。",
      defaults: { width: 704, height: 416, length: 49, fps: 24, steps: 18, cfg: 3.0 },
      uses_image: true,
      supported: true,
      model_label: "Mac LTX I2V 质量档",
    },
  ],
};

const macFallbackRecommendedModels = {
  ...fallbackRecommendedModels,
  draft: "mac_ltx_low_i2v",
  final: "mac_ltx_low_i2v",
};

const form = document.querySelector("#generateForm");
const statusStrip = document.querySelector("#statusStrip");
const statusText = document.querySelector("#statusText");
const modeInput = document.querySelector("#mode");
const stepTitle = document.querySelector("#stepTitle");
const stepSubtitle = document.querySelector("#stepSubtitle");
const stepBadge = document.querySelector("#stepBadge");
const stepGoal = document.querySelector("#stepGoal");
const promptInput = document.querySelector("#prompt");
const negativeInput = document.querySelector("#negative");
const imageInput = document.querySelector("#imageInput");
const videoInput = document.querySelector("#videoInput");
const stepsInput = document.querySelector("#steps");
const cfgInput = document.querySelector("#cfg");
const widthInput = document.querySelector("#width");
const heightInput = document.querySelector("#height");
const lengthInput = document.querySelector("#length");
const fpsInput = document.querySelector("#fps");
const sourceVideoFilename = document.querySelector("#sourceVideoFilename");
const sourceVideoSubfolder = document.querySelector("#sourceVideoSubfolder");
const sourceVideoType = document.querySelector("#sourceVideoType");
const sourceResolutionText = document.querySelector("#sourceResolutionText");
const targetResolutionLabel = document.querySelector("#targetResolutionLabel");
const targetResolutionText = document.querySelector("#targetResolutionText");
const targetResolutionNote = document.querySelector("#targetResolutionNote");
const environmentSummary = document.querySelector("#environmentSummary");
const environmentCards = document.querySelector("#environmentCards");
const modelRecommendations = document.querySelector("#modelRecommendations");
const loadDiagnostics = document.querySelector("#loadDiagnostics");
const missingItems = document.querySelector("#missingItems");
const smallModelRoutes = document.querySelector("#smallModelRoutes");
const prerequisiteChecks = document.querySelector("#prerequisiteChecks");
const bootstrapCards = document.querySelector("#bootstrapCards");
const hfEndpointInput = document.querySelector("#hfEndpointInput");
const pipIndexInput = document.querySelector("#pipIndexInput");
const proxyUrlInput = document.querySelector("#proxyUrlInput");
const saveDownloadSettingsButton = document.querySelector("#saveDownloadSettingsButton");
const testDownloadSourcesButton = document.querySelector("#testDownloadSourcesButton");
const downloadSourceStatus = document.querySelector("#downloadSourceStatus");
const refreshBootstrapButton = document.querySelector("#refreshBootstrapButton");
const installComfyButton = document.querySelector("#installComfyButton");
const startComfyButton = document.querySelector("#startComfyButton");
const detectEnvironmentButton = document.querySelector("#detectEnvironmentButton");
const runFullSetupButton = document.querySelector("#runFullSetupButton");
const runSelfTestButton = document.querySelector("#runSelfTestButton");
const runVideoSmokeButton = document.querySelector("#runVideoSmokeButton");
const installMissingButton = document.querySelector("#installMissingButton");
const installProfileSelector = document.querySelector("#installProfileSelector");
const modelSelector = document.querySelector("#modelSelector");
const modelChoiceHint = document.querySelector("#modelChoiceHint");
const workflowStatus = document.querySelector("#workflowStatus");
const modelLabelInput = document.querySelector("#modelLabel");
const modelProfileInput = document.querySelector("#modelProfile");
const upscaleModelInput = document.querySelector("#upscaleModel");
const upscaleScaleInput = document.querySelector("#upscaleScale");
const rifeMultiplierInput = document.querySelector("#rifeMultiplier");
const primaryButton = document.querySelector("#primaryButton");
const validateButton = document.querySelector("#validateButton");
const backButton = document.querySelector("#backButton");
const continueButton = document.querySelector("#continueButton");
const repeatButton = document.querySelector("#repeatButton");
const copyDiagnosticsButton = document.querySelector("#copyDiagnosticsButton");
const downloadDiagnosticsButton = document.querySelector("#downloadDiagnosticsButton");
const jobStatus = document.querySelector("#jobStatus");
const jobMeta = document.querySelector("#jobMeta");
const resultHint = document.querySelector("#resultHint");
const resultView = document.querySelector("#resultView");
const progressBar = document.querySelector("#progressBar");
const logBox = document.querySelector("#logBox");
const keyframePreview = document.querySelector("#keyframePreview");
const servicePanel = document.querySelector("#servicePanel");
const serviceModeBadge = document.querySelector("#serviceModeBadge");
const serviceFirstRun = document.querySelector("#serviceFirstRun");
const serviceServerUrl = document.querySelector("#serviceServerUrl");
const serviceAccessToken = document.querySelector("#serviceAccessToken");
const serviceLocalUrl = document.querySelector("#serviceLocalUrl");
const serviceLanUrl = document.querySelector("#serviceLanUrl");
const serviceApiUrl = document.querySelector("#serviceApiUrl");
const saveServiceConfigButton = document.querySelector("#saveServiceConfigButton");
const reloadServiceConfigButton = document.querySelector("#reloadServiceConfigButton");
const serviceStatus = document.querySelector("#serviceStatus");
const serviceModeInputs = Array.from(document.querySelectorAll('input[name="serviceMode"]'));

let activeIndex = 0;
let activeJobId = null;
let pollTimer = null;
let completedSteps = new Set();
let lastDraftVideo = null;
let lastFinalVideo = null;
let lastDeflickerVideo = null;
let lastRifeVideo = null;
let lastUpscaleVideo = null;
let currentVideoResolution = null;
let environmentData = null;
let installPollTimer = null;
let selectedModels = {};
let currentVideoFps = 24;
let workflowCheckController = null;
let workflowPreflightTimer = null;
let bootstrapData = null;
let selectedInstallProfile = "auto";
let installPlanRequestId = 0;
let bootstrapPollTimer = null;
let fullSetupPollTimer = null;
let selfTestPollTimer = null;
let videoSmokePollTimer = null;
let allowDefaultKeyframe = false;
let allowComfyInstall = false;
let allowFullSetup = false;
let allowWorkflowAssetInstall = false;

const comfyInstallButtonText = "安装/更新 ComfyUI";
const comfyInstallConfirmText = "确认安装/更新 ComfyUI";
const fullSetupButtonText = "一键准备环境";
const fullSetupConfirmText = "确认一键准备";
const workflowAssetButtonText = "一键安装/修复缺失项";
const workflowAssetConfirmText = "确认安装/修复缺失项";

function setComfyInstallConfirming(value) {
  allowComfyInstall = Boolean(value);
  if (installComfyButton) {
    installComfyButton.textContent = allowComfyInstall ? comfyInstallConfirmText : comfyInstallButtonText;
  }
}

function setFullSetupConfirming(value) {
  allowFullSetup = Boolean(value);
  if (runFullSetupButton) {
    runFullSetupButton.textContent = allowFullSetup ? fullSetupConfirmText : fullSetupButtonText;
  }
}

function setWorkflowAssetInstallConfirming(value) {
  allowWorkflowAssetInstall = Boolean(value);
  if (installMissingButton) {
    installMissingButton.textContent = allowWorkflowAssetInstall ? workflowAssetConfirmText : workflowAssetButtonText;
  }
}

function normalizeServicePayload(payload = {}) {
  const mode = serviceModeLabels[payload.mode] ? payload.mode : "both";
  return {
    ...serviceConfig,
    ...payload,
    mode,
    server_url: cleanBaseUrl(payload.server_url || ""),
    api_base_url: cleanBaseUrl(payload.api_base_url || (mode === "client" ? payload.server_url : "")),
    access_token: String(payload.access_token || ""),
  };
}

function selectedServiceMode() {
  return serviceModeInputs.find((input) => input.checked)?.value || serviceConfig.mode || "both";
}

function setServiceButtonsDisabled(disabled) {
  if (saveServiceConfigButton) saveServiceConfigButton.disabled = disabled;
  if (reloadServiceConfigButton) reloadServiceConfigButton.disabled = disabled;
}

function syncServiceInputs(config, { force = false } = {}) {
  serviceModeInputs.forEach((input) => {
    input.checked = input.value === config.mode;
    input.closest(".service-mode-card")?.classList.toggle("active", input.checked);
  });
  if (serviceServerUrl && (force || document.activeElement !== serviceServerUrl)) {
    serviceServerUrl.value = config.server_url || "";
  }
  if (serviceAccessToken && (force || document.activeElement !== serviceAccessToken)) {
    serviceAccessToken.value = config.access_token || "";
  }
  if (serviceServerUrl) {
    serviceServerUrl.disabled = config.mode !== "client";
    serviceServerUrl.required = config.mode === "client";
  }
}

function serviceStatusMessage(config) {
  if (config.mode === "client") {
    if (!config.server_url) return "客户端模式需要填写服务端地址，例如 http://192.168.1.20:7860。";
    return `客户端模式已启用。生成、安装、自检和诊断 API 会提交到 ${config.server_url}。`;
  }
  if (!config.mode_configured) {
    return "当前使用默认本机一体模式。保存后会固定为开箱入口，并自动准备局域网访问令牌。";
  }
  if (config.binds_lan) {
    return `服务端监听已配置。局域网电脑可打开 ${config.lan_url || "本机显示的 LAN 地址"}，写操作需要访问令牌。`;
  }
  return "服务端模式已保存；请重启 start.bat 让前端监听局域网地址。";
}

function renderServiceConfig(config = serviceConfig, { message = "", state = "" } = {}) {
  if (!servicePanel) return;
  serviceConfig = normalizeServicePayload(config);
  syncServiceInputs(serviceConfig);
  const label = serviceModeLabels[serviceConfig.mode] || "未知";
  if (serviceModeBadge) {
    serviceModeBadge.textContent = serviceConfig.mode_configured ? label : `${label} · 待保存`;
  }
  serviceFirstRun?.classList.toggle("hidden", !serviceConfig.first_run_required);
  if (serviceLocalUrl) serviceLocalUrl.textContent = serviceConfig.local_url || "本机启动后显示";
  if (serviceLanUrl) {
    if (serviceConfig.first_run_required) {
      serviceLanUrl.textContent = "保存并重启后显示";
    } else if (serviceConfig.mode === "client") {
      serviceLanUrl.textContent = "客户端不监听局域网";
    } else {
      serviceLanUrl.textContent = serviceConfig.lan_url || "重启服务端模式后显示";
    }
  }
  if (serviceApiUrl) {
    serviceApiUrl.textContent = serviceConfig.mode === "client" ? serviceConfig.server_url || "未填写" : "本机后端";
  }
  if (serviceStatus) {
    const statusState =
      state ||
      (serviceConfig.mode === "client" && !serviceConfig.server_url
        ? "bad"
        : serviceConfig.first_run_required
          ? "pending"
          : "ok");
    serviceStatus.className = `workflow-status ${statusState}`;
    serviceStatus.textContent = message || serviceStatusMessage(serviceConfig);
  }
}

function previewServiceConfig() {
  renderServiceConfig({
    ...serviceConfig,
    mode: selectedServiceMode(),
    server_url: serviceServerUrl?.value.trim() || "",
    access_token: serviceAccessToken?.value.trim() || serviceConfig.access_token || "",
  });
}

async function loadClientConfig({ quiet = true, pingRemote = false } = {}) {
  try {
    const response = await nativeFetch("/api/client-config", { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(formatError(data.detail || data));
    renderServiceConfig(data.service || {}, { state: data.service?.first_run_required ? "pending" : "ok" });
    if (!quiet) appendLog("运行入口配置已刷新。");
    if (pingRemote && serviceConfig.mode === "client" && serviceConfig.server_url) {
      await checkRemoteService();
    }
    return serviceConfig;
  } catch (error) {
    if (serviceStatus) {
      serviceStatus.className = "workflow-status bad";
      serviceStatus.textContent = `运行入口配置读取失败：${error.message}`;
    }
    if (!quiet) appendLog(`运行入口配置读取失败：${error.message}`);
    return serviceConfig;
  }
}

async function checkRemoteService() {
  if (serviceConfig.mode !== "client" || !serviceConfig.server_url) return;
  if (serviceStatus) {
    serviceStatus.className = "workflow-status pending";
    serviceStatus.textContent = `正在连接服务端 ${serviceConfig.server_url}。`;
  }
  try {
    const response = await fetch("/api/health", { cache: "no-store" });
    const data = await response.json();
    if (!response.ok) throw new Error(formatError(data.detail || data));
    if (serviceStatus) {
      serviceStatus.className = "workflow-status ok";
      serviceStatus.textContent = `服务端已连接：${data.service?.mode || "server"}，ComfyUI ${data.comfy_connected ? "已连接" : "未连接"}。`;
    }
  } catch (error) {
    if (serviceStatus) {
      serviceStatus.className = "workflow-status bad";
      serviceStatus.textContent = `服务端连接失败：${error.message}`;
    }
  }
}

async function saveServiceConfig() {
  if (!saveServiceConfigButton) return;
  setServiceButtonsDisabled(true);
  if (serviceStatus) {
    serviceStatus.className = "workflow-status pending";
    serviceStatus.textContent = "正在保存运行入口配置。";
  }
  const payload = {
    mode: selectedServiceMode(),
    server_url: serviceServerUrl?.value.trim() || "",
    access_token: serviceAccessToken?.value.trim() || "",
  };
  try {
    const response = await nativeFetch("/api/service-config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ service: payload }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(formatError(data.detail || data));
    const restartText = data.restart_required ? " 保存成功；请重启 start.bat 让监听地址生效。" : " 保存成功。";
    renderServiceConfig(data.service || {}, { state: data.restart_required ? "pending" : "ok", message: `${serviceStatusMessage(data.service || {})}${restartText}` });
    appendLog(`运行入口配置已保存：${serviceModeLabels[serviceConfig.mode] || serviceConfig.mode}。`);
    if (serviceConfig.mode === "client" && serviceConfig.server_url) {
      await checkRemoteService();
    }
  } catch (error) {
    if (serviceStatus) {
      serviceStatus.className = "workflow-status bad";
      serviceStatus.textContent = `运行入口配置保存失败：${error.message}`;
    }
    appendLog(`运行入口配置保存失败：${error.message}`);
  } finally {
    setServiceButtonsDisabled(false);
  }
}

function currentStep() {
  return steps[activeIndex];
}

function setLog(message) {
  logBox.textContent = message || "";
}

function appendLog(message) {
  const time = new Date().toLocaleTimeString();
  logBox.textContent = `${logBox.textContent}${logBox.textContent ? "\n" : ""}[${time}] ${message}`;
  logBox.scrollTop = logBox.scrollHeight;
}

function setStatus(ok, text) {
  const dot = statusStrip.querySelector(".dot");
  dot.className = `dot ${ok ? "" : "bad"}`;
  statusText.textContent = text;
}

function showSections(sectionNames) {
  document.querySelectorAll("[data-section]").forEach((section) => {
    section.classList.toggle("hidden", !sectionNames.includes(section.dataset.section));
  });
}

function updateStepCopy() {
  const step = currentStep();
  const option = selectedModelOption(step.id);
  const strategy = environmentData?.hardware?.platform_strategy || (clientLooksLikeMac() ? "mac_mps" : "");
  let title = step.title;
  let subtitle = step.subtitle;
  let goal = step.goal;
  let primary = step.primary;

  if (step.id === "keyframe") {
    primary = `确认关键帧，进入 ${selectedModelName("draft")}`;
  } else if (step.id === "draft") {
    if (option?.mode === "ltx_i2v") {
      title = "3. Mac LTX 试镜头";
      subtitle = "先用 Mac 更容易跑通的小模型验证动作和镜头。";
      goal = "这一关用 LTX 小分辨率短镜头做草稿，先确认运动方向和镜头节奏，再进入正式片段。";
    } else {
      title = "3. Wan5B 试镜头";
      subtitle = "先快速看动作、镜头运动和 Prompt 是否靠谱。";
    }
  } else if (step.id === "final") {
    if (strategy === "mac_mps" || option?.mode?.startsWith("ltx")) {
      title = "4. Mac 视频正式片段";
      subtitle = "在当前 Mac 推荐档位上生成可剪辑短片段。";
      goal = "这一关会沿用第一步推荐的 Mac 路线。优先短镜头、关键帧控制和后期增强，不默认冒险使用 A14B。";
    } else if (option?.mode === "ti2v_5b") {
      title = "4. Wan5B 轻量正片";
      subtitle = "显存不够 A14B 时，用 5B 生成更稳的正式片段。";
    } else {
      title = "4. A14B 正式片段";
      subtitle = "草稿方向确认后，再用大模型出 720P 正片。";
    }
  } else if (step.id === "rife") {
    const multiplier = Number(selectedModelOption("rife")?.rife_multiplier || rifeMultiplierInput.value || 2);
    title = `6. RIFE ${multiplier}x 插帧`;
    subtitle = `把视频插到约 ${Number(fpsInput.value || currentVideoFps || 24) * multiplier}fps，运动更顺。`;
  } else if (step.id === "upscale") {
    const scale = getUpscaleScale();
    title = `7. 清晰度增强 ${scale}x`;
    subtitle = `目标分辨率会按输入视频实际尺寸放大 ${scale} 倍。`;
  }

  stepTitle.textContent = title;
  stepSubtitle.textContent = subtitle;
  stepBadge.textContent = step.badge;
  stepGoal.textContent = goal;
  primaryButton.textContent = primary;
}

function setStep(stepId) {
  const nextIndex = steps.findIndex((item) => item.id === stepId);
  if (nextIndex < 0) return;
  activeIndex = nextIndex;
  const step = currentStep();

  stepTitle.textContent = step.title;
  stepSubtitle.textContent = step.subtitle;
  stepBadge.textContent = step.badge;
  stepGoal.textContent = step.goal;
  modeInput.value = step.mode;
  primaryButton.textContent = step.primary;

  showSections(step.sections);
  applyStepDefaults(step);
  renderModelSelector();
  updateStepCopy();
  updateStepper();
  updateNavigation();
  updateVideoSource();
  renderStepIdle();
  if (step.id === "environment") {
    loadClientConfig();
    loadSettings();
    loadBootstrap();
    loadEnvironment();
  }
}

function applyStepDefaults(step) {
  if (!promptInput.value.trim()) promptInput.value = defaultPrompt;
  if (!negativeInput.value.trim()) negativeInput.value = defaultNegative;
  if (!step.defaults) return;
  if (step.defaults.steps !== undefined) stepsInput.value = step.defaults.steps;
  if (step.defaults.cfg !== undefined) cfgInput.value = step.defaults.cfg;
  if (step.defaults.fps !== undefined) fpsInput.value = step.defaults.fps;
}

function updateStepper() {
  document.querySelectorAll(".step-button").forEach((button) => {
    const id = button.dataset.step;
    button.classList.toggle("active", id === currentStep().id);
    button.classList.toggle("complete", completedSteps.has(id));
  });
}

function updateNavigation() {
  backButton.classList.toggle("hidden", activeIndex === 0);
  continueButton.classList.add("hidden");
  repeatButton.classList.add("hidden");
  primaryButton.classList.remove("hidden");
}

function renderStepIdle() {
  const step = currentStep();
  setProgress("");
  jobStatus.textContent = completedSteps.has(step.id) ? "已完成" : "空闲";

  if (step.id === "environment") {
    jobMeta.innerHTML = "<span>自动检测</span><span>Windows / macOS 兼容</span>";
    resultHint.textContent = "先确认环境，再进入关键帧。";
    resultView.innerHTML = "<p>环境侦测结果会显示在左侧。</p>";
  } else if (step.id === "keyframe") {
    jobMeta.innerHTML = `<span>${escapeHtml(selectedModelName())}</span><span>下一步：${escapeHtml(selectedModelName("draft"))}</span>`;
    resultHint.textContent = "确认关键帧后会进入试镜头。";
    resultView.innerHTML = "<p>关键帧预览在左侧，视频输出会显示在这里。</p>";
  } else if (step.id === "draft") {
    jobMeta.innerHTML = `<span>${escapeHtml(selectedModelName())}</span><span>建议先用短镜头</span>`;
    resultHint.textContent = `草稿完成后，点击继续进入 ${selectedModelName("final")}。`;
    renderMedia(lastDraftVideo ? [lastDraftVideo] : []);
  } else if (step.id === "final") {
    jobMeta.innerHTML = `<span>${escapeHtml(selectedModelName())}</span><span>推荐短镜头</span>`;
    resultHint.textContent = "正片完成后，点击继续进入画面闪烁修复。";
    renderMedia(lastFinalVideo ? [lastFinalVideo] : []);
  } else if (step.id === "deflicker") {
    const source = lastFinalVideo || lastDraftVideo;
    jobMeta.innerHTML = source
      ? `<span>${escapeHtml(selectedModelName())}</span><span>已找到上一阶段视频</span>`
      : "<span>请上传待修复视频</span>";
    resultHint.textContent = "修复完成后，点击继续进入 RIFE 插帧。";
    renderMedia(lastDeflickerVideo ? [lastDeflickerVideo] : source ? [source] : []);
  } else if (step.id === "rife") {
    const source = lastDeflickerVideo || lastFinalVideo || lastDraftVideo;
    jobMeta.innerHTML = source
      ? `<span>${escapeHtml(selectedModelName())}</span><span>已找到上一阶段视频</span>`
      : "<span>请上传待插帧视频</span>";
    resultHint.textContent = "插帧完成后，点击继续进入清晰度增强。";
    renderMedia(lastRifeVideo ? [lastRifeVideo] : source ? [source] : []);
  } else if (step.id === "upscale") {
    const source = lastRifeVideo || lastDeflickerVideo || lastFinalVideo || lastDraftVideo;
    jobMeta.innerHTML = source
      ? `<span>${escapeHtml(selectedModelName())}</span><span>目标 ${getUpscaleScale()}x 超分</span>`
      : "<span>请上传待增强视频</span>";
    resultHint.textContent = "清晰度增强完成后，点击继续进入剪辑整理。";
    renderMedia(lastUpscaleVideo ? [lastUpscaleVideo] : source ? [source] : []);
  } else {
    jobMeta.innerHTML = `<span>输出目录</span><span>${escapeHtml(outputDirectoryText())}</span>`;
    resultHint.textContent = "多做几个镜头后，在剪辑软件里拼成完整视频。";
    renderMedia(lastUpscaleVideo ? [lastUpscaleVideo] : lastRifeVideo ? [lastRifeVideo] : lastDeflickerVideo ? [lastDeflickerVideo] : lastFinalVideo ? [lastFinalVideo] : lastDraftVideo ? [lastDraftVideo] : []);
    continueButton.classList.add("hidden");
  }
}

function setSize(width, height) {
  widthInput.value = width;
  heightInput.value = height;
  document.querySelectorAll("#sizePreset button").forEach((button) => {
    button.classList.toggle(
      "active",
      Number(button.dataset.width) === Number(width) &&
      Number(button.dataset.height) === Number(height),
    );
  });
  if (!currentVideoResolution) updateTargetResolution();
  scheduleWorkflowPreflight();
}

function setLength(length) {
  lengthInput.value = length;
  document.querySelectorAll("#durationPreset button").forEach((button) => {
    button.classList.toggle("active", Number(button.dataset.length) === Number(length));
  });
  scheduleWorkflowPreflight();
}

function setJobMeta(job) {
  const parts = [
    job.title || "任务",
    `Seed ${job.seed}`,
    `${job.width || "-"} x ${job.height || "-"}`,
    `${job.length || "-"} 帧`,
    `${job.fps || "-"} fps`,
  ];
  jobMeta.innerHTML = parts.map((part) => `<span>${escapeHtml(String(part))}</span>`).join("");
}

function setProgress(status) {
  progressBar.classList.remove("running");
  if (status === "success") {
    progressBar.style.width = "100%";
  } else if (status === "running") {
    progressBar.style.width = "66%";
    progressBar.classList.add("running");
  } else if (status === "queued") {
    progressBar.style.width = "22%";
  } else if (status) {
    progressBar.style.width = "100%";
  } else {
    progressBar.style.width = "0";
  }
}

function escapeHtml(value) {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function labelForStatus(status) {
  if (status === "ok") return "可用";
  if (status === "warn") return "建议调整";
  if (status === "blocked") return "需处理";
  return status || "未知";
}

function boolText(value) {
  return value ? "已就绪" : "未就绪";
}

function formatGb(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "-";
  return `${Number(value).toFixed(Number(value) >= 10 ? 0 : 1)} GB`;
}

function renderDownloadSettings(settings = {}) {
  if (!downloadSourceStatus) return;
  const endpoint = settings.hf_endpoint || "";
  const saved = settings.saved_hf_endpoint || "";
  const envEndpoint = settings.env_hf_endpoint || "";
  const source = settings.source || (endpoint ? "saved" : "official");
  const proxyUrl = settings.proxy_url || "";
  const savedProxy = settings.saved_proxy_url || "";
  const envProxy = settings.env_proxy_url || "";
  const proxySource = settings.proxy_source || "none";
  const pipIndex = settings.pip_index_url || "";
  const savedPipIndex = settings.saved_pip_index_url || "";
  const envPipIndex = settings.env_pip_index_url || "";
  const pipIndexSource = settings.pip_index_source || "official";
  if (hfEndpointInput && document.activeElement !== hfEndpointInput) {
    hfEndpointInput.value = saved || endpoint || "";
  }
  if (pipIndexInput && document.activeElement !== pipIndexInput) {
    pipIndexInput.value = savedPipIndex || pipIndex || "";
  }
  if (settings.sensitive && proxyUrlInput && document.activeElement !== proxyUrlInput) {
    proxyUrlInput.value = savedProxy || proxyUrl || "";
  }
  let message = "当前使用官方 Hugging Face 下载源。";
  let state = "pending";
  if (source === "environment") {
    message = `当前由环境变量指定：${envEndpoint}。页面保存的地址会作为下次启动兜底，不会覆盖环境变量。`;
    state = "ok";
  } else if (source === "saved") {
    message = `当前使用已保存下载源：${endpoint}。一键准备、ComfyUI 安装、依赖安装、安装缺失项和网络预检都会使用它。`;
    state = "ok";
  }
  if (pipIndexSource === "environment") {
    message += ` pip 镜像由环境变量指定：${envPipIndex}。`;
    state = "ok";
  } else if (pipIndexSource === "saved") {
    message += ` 当前使用已保存 pip 镜像：${pipIndex}。`;
    state = "ok";
  } else {
    message += " pip 使用官方 PyPI。";
  }
  if (proxySource === "environment") {
    message += ` 代理由环境变量指定：${envProxy}。`;
    state = "ok";
  } else if (proxySource === "saved") {
    message += ` 当前使用已保存代理：${proxyUrl}。`;
    state = "ok";
  } else {
    message += " 当前未设置代理。";
  }
  downloadSourceStatus.className = `workflow-status ${state}`;
  downloadSourceStatus.textContent = message;
}

async function loadSettings() {
  try {
    const response = await fetch("/api/settings");
    const data = await response.json();
    if (!response.ok) throw new Error(formatError(data.detail || data));
    renderDownloadSettings(data.download);
    return data.download;
  } catch (error) {
    if (downloadSourceStatus) {
      downloadSourceStatus.className = "workflow-status bad";
      downloadSourceStatus.textContent = `网络设置读取失败：${error.message}`;
    }
    return null;
  }
}

function downloadSettingsPayloadFromInputs() {
  return {
    hf_endpoint: hfEndpointInput?.value.trim() || "",
    pip_index_url: pipIndexInput?.value.trim() || "",
    proxy_url: proxyUrlInput?.value.trim() || "",
  };
}

function setDownloadSettingsButtonsDisabled(disabled) {
  if (saveDownloadSettingsButton) saveDownloadSettingsButton.disabled = disabled;
  if (testDownloadSourcesButton) testDownloadSourcesButton.disabled = disabled;
}

async function persistDownloadSettings({ refreshAfterSave = true } = {}) {
  const response = await fetch("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ download: downloadSettingsPayloadFromInputs() }),
  });
  const data = await response.json();
  if (!response.ok) throw new Error(formatError(data.detail || data));
  if (environmentData) {
    environmentData.download_settings = data.download;
  }
  renderDownloadSettings(data.download);
  if (refreshAfterSave) {
    await loadBootstrap();
    if (environmentData) {
      await loadInstallPlan(selectedInstallProfile);
    }
  }
  return data.download;
}

async function saveDownloadSettings() {
  if (!saveDownloadSettingsButton || !hfEndpointInput) return;
  setDownloadSettingsButtonsDisabled(true);
  downloadSourceStatus.className = "workflow-status pending";
  downloadSourceStatus.textContent = "正在保存下载源设置。";
  try {
    await persistDownloadSettings();
    appendLog("下载源设置已保存。");
  } catch (error) {
    downloadSourceStatus.className = "workflow-status bad";
    downloadSourceStatus.textContent = `下载源保存失败：${error.message}`;
    appendLog(`下载源保存失败：${error.message}`);
  } finally {
    setDownloadSettingsButtonsDisabled(false);
  }
}

function renderDownloadSourceTest(report) {
  const networkCheck = (report?.checks || []).find((item) => item.id === "network");
  if (!downloadSourceStatus || !networkCheck) return;
  const targets = networkCheck.targets || {};
  const reachable = networkCheck.reachable || {};
  const details = Object.entries(targets)
    .map(([host, target]) => `${target.label || host}：${reachable[host] ? "可达" : "不可达"}`)
    .join("；");
  const state = networkCheck.status === "ok" ? "ok" : "bad";
  downloadSourceStatus.className = `workflow-status ${state}`;
  downloadSourceStatus.textContent = `${networkCheck.message || "下载源测试完成。"}${details ? ` ${details}` : ""}`;
}

async function testDownloadSources() {
  if (!testDownloadSourcesButton) return;
  setDownloadSettingsButtonsDisabled(true);
  if (downloadSourceStatus) {
    downloadSourceStatus.className = "workflow-status pending";
    downloadSourceStatus.textContent = "正在保存当前填写的下载源并测试连通性。";
  }
  try {
    await persistDownloadSettings({ refreshAfterSave: false });
    appendLog("下载源设置已保存，开始测试。");
    await loadBootstrap();
    if (environmentData) {
      await loadInstallPlan(selectedInstallProfile);
    }
    const response = await fetch("/api/prerequisites?network=true");
    const data = await response.json();
    if (!response.ok) throw new Error(formatError(data.detail || data));
    renderPrerequisites(data);
    renderDownloadSourceTest(data);
    appendLog("下载源测试完成。");
  } catch (error) {
    if (downloadSourceStatus) {
      downloadSourceStatus.className = "workflow-status bad";
      downloadSourceStatus.textContent = `下载源保存或测试失败：${error.message}`;
    }
    appendLog(`下载源保存或测试失败：${error.message}`);
  } finally {
    setDownloadSettingsButtonsDisabled(false);
  }
}

function platformStrategyLabel(strategy) {
  const labels = {
    cuda_wan_workflow: "CUDA Wan 主线",
    mac_mps: "Mac MPS 视频路线",
    mac_post_only: "Mac 后期/轻量路线",
    post_only: "后期处理路线",
  };
  return labels[strategy] || strategy || "未识别";
}

function macTierLabel(tier) {
  const labels = {
    mac_ltx_low: "LTX 低档",
    mac_ltx_balanced: "LTX 均衡档",
    mac_ltx_quality: "LTX 质量档",
    mac_wan5b_480p: "Wan5B 480P 实验",
    mac_wan5b_720p_experimental: "Wan5B 720P 实验",
    mac_post_only: "后期处理",
  };
  return labels[tier] || tier || "-";
}

function clientLooksLikeMac() {
  return /Mac|iPhone|iPad/.test(navigator.platform || "") || /Mac OS/.test(navigator.userAgent || "");
}

function fallbackOptionsForStep(stepId) {
  return (clientLooksLikeMac() ? macFallbackModelOptions : fallbackModelOptions)[stepId] || [];
}

function fallbackRecommendedForStep(stepId) {
  return (clientLooksLikeMac() ? macFallbackRecommendedModels : fallbackRecommendedModels)[stepId] || "";
}

function modelOptionsForStep(stepId) {
  return (
    environmentData?.model_options?.options?.[stepId] ||
    fallbackOptionsForStep(stepId)
  );
}

function recommendedModelForStep(stepId) {
  return (
    environmentData?.model_options?.recommended?.[stepId] ||
    fallbackRecommendedForStep(stepId) ||
    modelOptionsForStep(stepId)[0]?.id ||
    ""
  );
}

function selectedModelOption(stepId = currentStep().id) {
  const options = modelOptionsForStep(stepId);
  if (!options.length) return null;
  const selectedId = selectedModels[stepId] || recommendedModelForStep(stepId);
  return options.find((item) => item.id === selectedId) || options[0];
}

function applyModelDefaults(option) {
  const defaults = option?.defaults || {};
  if (defaults.width && defaults.height) {
    setSize(defaults.width, defaults.height);
  }
  if (defaults.length) setLength(defaults.length);
  if (defaults.fps !== undefined) fpsInput.value = defaults.fps;
  if (defaults.steps !== undefined) stepsInput.value = defaults.steps;
  if (defaults.cfg !== undefined) cfgInput.value = defaults.cfg;
}

function applyModelVisibility(option) {
  const step = currentStep();
  const hideImage = step.id === "final" && option && option.uses_image === false;
  document.querySelectorAll('[data-section="image"], [data-section="keyframePreview"]').forEach((section) => {
    section.classList.toggle("hidden", hideImage || !step.sections.includes(section.dataset.section));
  });
}

function getUpscaleScale() {
  const option = selectedModelOption("upscale");
  return Number(option?.scale || upscaleScaleInput.value || 2);
}

function syncSelectedModel({ applyDefaults = false } = {}) {
  const step = currentStep();
  const option = selectedModelOption(step.id);
  if (!option) {
    modelLabelInput.value = "";
    modelProfileInput.value = "";
    return;
  }

  selectedModels[step.id] = option.id;
  modeInput.value = option.mode || step.mode || "";
  modelLabelInput.value = option.model_label || option.label || "";
  modelProfileInput.value = option.id || "";
  upscaleModelInput.value = option.upscale_model || "RealESRGAN_x2plus.pth";
  upscaleScaleInput.value = option.scale || 2;
  rifeMultiplierInput.value = option.rife_multiplier || 2;
  modelChoiceHint.textContent = `${option.reason || "已选择该模型。"} 状态：${labelForStatus(option.status)}。`;

  if (applyDefaults) {
    applyModelDefaults(option);
  }
  applyModelVisibility(option);
  updateTargetResolution();
  updateStepCopy();
  scheduleWorkflowPreflight();
}

function renderModelSelector() {
  const step = currentStep();
  const options = modelOptionsForStep(step.id);
  if (!modelSelector || !options.length) return;

  if (!selectedModels[step.id]) {
    selectedModels[step.id] = recommendedModelForStep(step.id);
  }
  modelSelector.innerHTML = options
    .map((item) => {
      const disabled = item.status === "blocked" && step.id !== "keyframe";
      const marker = item.id === recommendedModelForStep(step.id) ? "推荐" : labelForStatus(item.status);
      return `<option value="${escapeHtml(item.id)}" ${disabled ? "disabled" : ""}>${escapeHtml(item.label)} · ${escapeHtml(marker)}</option>`;
    })
    .join("");
  modelSelector.value = selectedModels[step.id];
  if (modelSelector.value !== selectedModels[step.id]) {
    selectedModels[step.id] = options.find((item) => item.status !== "blocked")?.id || options[0].id;
    modelSelector.value = selectedModels[step.id];
  }
  syncSelectedModel({ applyDefaults: true });
}

function selectedModelName(stepId = currentStep().id) {
  const option = selectedModelOption(stepId);
  return option?.label || "默认模型";
}

function joinDisplayPath(base, ...parts) {
  if (!base) return ["ComfyUI 用户目录", ...parts].join("/");
  const separator = base.includes("\\") ? "\\" : "/";
  return [base.replace(/[\\/]+$/, ""), ...parts].join(separator);
}

function outputDirectoryText() {
  return joinDisplayPath(environmentData?.base_dir, "output", "wan22_frontend");
}

function workflowStatusHtml(message, errors = []) {
  const safeMessage = escapeHtml(String(message || ""));
  const safeErrors = (errors || []).filter(Boolean).map((item) => `<li>${escapeHtml(String(item))}</li>`).join("");
  if (!safeErrors) return safeMessage;
  return `${safeMessage ? `<strong>${safeMessage}</strong>` : "<strong>workflow 预检未通过</strong>"}<ul>${safeErrors}</ul>`;
}

function setWorkflowStatus(state, message, errors = []) {
  if (!workflowStatus) return;
  workflowStatus.className = `workflow-status ${state || ""}`.trim();
  workflowStatus.innerHTML = workflowStatusHtml(message, errors);
}

function workflowParams() {
  return new URLSearchParams({
    mode: modeInput.value || "",
    model_profile: modelProfileInput.value || "",
    model_label: modelLabelInput.value || "",
    width: widthInput.value || "1280",
    height: heightInput.value || "704",
    length: lengthInput.value || "81",
    fps: fpsInput.value || "24",
    seed: "1",
    steps: stepsInput.value || "4",
    cfg: cfgInput.value || "1",
    upscale_model: upscaleModelInput.value || "RealESRGAN_x2plus.pth",
    rife_multiplier: rifeMultiplierInput.value || "2",
    has_image: imageInput.files && imageInput.files.length ? "1" : "0",
  });
}

async function preflightWorkflow({ quiet = true } = {}) {
  const step = currentStep();
  if (!step.sections.includes("modelChoice")) return true;

  if (workflowCheckController) {
    workflowCheckController.abort();
  }
  workflowCheckController = new AbortController();
  const optionName = selectedModelName();
  setWorkflowStatus("pending", `正在拉取 ${optionName} 对应的 workflow。`);
  try {
    const response = await fetch(`/api/workflow?${workflowParams().toString()}`, {
      signal: workflowCheckController.signal,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(formatError(data.detail || data));
    if (data.ok) {
      const detail = data.local
        ? "本地处理，无需 ComfyUI 图。"
        : `${data.node_count || 0} 个节点，输出节点 ${data.output_nodes?.join(", ") || "-"}`;
      const warnings = (data.risk_checks || []).filter((item) => item.level === "warn");
      const warningText = warnings.length ? ` 提醒：${warnings.map((item) => item.title || item.message).join("、")}` : "";
      setWorkflowStatus(warnings.length ? "pending" : "ok", `${data.message} ${detail}${warningText}`);
      return true;
    }
    const errors = data.errors || [];
    const message = data.message || "workflow 预检未通过";
    setWorkflowStatus("bad", message, errors);
    const logMessage = errors.length ? `${message}\n${errors.join("\n")}` : message;
    if (!quiet) appendLog(`workflow 预检失败：${logMessage}`);
    return false;
  } catch (error) {
    if (error.name === "AbortError") return false;
    setWorkflowStatus("bad", `workflow 拉取失败：${error.message}`);
    if (!quiet) appendLog(`workflow 拉取失败：${error.message}`);
    return false;
  }
}

function scheduleWorkflowPreflight() {
  if (!currentStep().sections.includes("modelChoice")) return;
  if (workflowPreflightTimer) clearTimeout(workflowPreflightTimer);
  workflowPreflightTimer = window.setTimeout(() => {
    preflightWorkflow();
  }, 250);
}

function renderEnvironmentCards(data) {
  const os = data.os || {};
  const comfy = data.comfy || {};
  const paths = data.paths || {};
  const hardware = data.hardware || {};
  const mac = hardware.mac || {};
  const devices = hardware.devices || [];
  const firstDevice = devices[0] || {};
  const ffmpeg = (data.tools || []).find((item) => item.name === "ffmpeg");
  const missingCount = (data.missing_installable || []).length;
  const gpuLabel = devices.length
    ? `${firstDevice.name}${devices.length > 1 ? ` +${devices.length - 1}` : ""}`
    : hardware.accelerator === "mps"
      ? "Apple Metal / MPS"
      : "未检测到独立 GPU";
  const vramLabel = hardware.max_vram_gb
    ? `最高单卡 ${formatGb(hardware.max_vram_gb)}`
    : hardware.system_memory_gb
      ? `内存 ${formatGb(hardware.system_memory_gb)}`
      : "显存未知";
  const ffmpegDetail = ffmpeg && ffmpeg.ok
    ? `${ffmpeg.source || "ffmpeg"} | ${ffmpeg.path || ffmpeg.version || "已就绪"}`
    : (ffmpeg && (ffmpeg.message || ffmpeg.install_hint)) || "闪烁修复需要 ffmpeg";

  const cards = [
    {
      label: "操作系统",
      value: `${os.name || "-"} ${os.release || ""}`.trim(),
      detail: `${os.machine || "-"} | Python ${os.python || "-"}`,
      state: "ok",
    },
    {
      label: "GPU / 显存",
      value: mac.is_macos && mac.chip ? mac.chip : gpuLabel,
      detail: mac.is_macos
        ? `统一内存 ${formatGb(mac.unified_memory_gb || hardware.system_memory_gb)} | MPS ${hardware.comfy_torch_mps_ready || hardware.front_torch_mps_ready ? "已确认" : "待确认"}`
        : `${vramLabel}${hardware.sum_vram_gb ? ` | 总计 ${formatGb(hardware.sum_vram_gb)}` : ""}`,
      state: hardware.accelerator === "cpu" ? "blocked" : hardware.max_vram_gb >= 80 ? "ok" : "warn",
    },
    {
      label: "平台策略",
      value: platformStrategyLabel(hardware.platform_strategy),
      detail: mac.is_macos ? `${macTierLabel(hardware.mac_video_tier)} | 安装 ${data.install_profile || "auto"}` : `安装 ${data.install_profile || "auto"}`,
      state: hardware.platform_strategy === "cuda_wan_workflow" || hardware.platform_strategy === "mac_mps" ? "ok" : "warn",
    },
    {
      label: "ComfyUI",
      value: comfy.connected ? comfy.comfyui_version || "已连接" : "未连接",
      detail: comfy.connected ? `队列 ${comfy.queue?.running || 0}/${comfy.queue?.pending || 0}` : comfy.error || "请先启动 ComfyUI",
      state: comfy.connected ? "ok" : "blocked",
    },
    {
      label: "ffmpeg",
      value: boolText(ffmpeg && ffmpeg.ok),
      detail: ffmpegDetail,
      state: ffmpeg && ffmpeg.ok ? "ok" : "blocked",
    },
    {
      label: "缺失项",
      value: missingCount ? `${missingCount} 项` : "0 项",
      detail: data.repair_needed ? "可点击一键修复加载问题" : missingCount ? "可点击一键安装补齐" : "模型和节点已就绪",
      state: missingCount ? "warn" : "ok",
    },
    {
      label: "工作目录",
      value: paths.base_dir_mismatch ? "已跟随 ComfyUI" : "目录一致",
      detail: paths.active_base_dir || data.base_dir || "-",
      state: paths.base_dir_mismatch ? "warn" : "ok",
    },
  ];

  environmentCards.innerHTML = cards
    .map(
      (card) => `
        <div class="env-card ${card.state}">
          <small>${escapeHtml(card.label)}</small>
          <strong>${escapeHtml(card.value)}</strong>
          <span>${escapeHtml(card.detail)}</span>
        </div>
      `,
    )
    .join("");
}

function renderLoadDiagnostics(data) {
  if (!loadDiagnostics) return;
  const diagnostics = data.diagnostics || [];
  if (!diagnostics.length) {
    loadDiagnostics.innerHTML = `<p class="empty-state">暂无加载诊断结果。</p>`;
    return;
  }
  const paths = data.paths || {};
  const pathRows = [
    ["ComfyUI base", paths.active_base_dir || data.base_dir],
    ["input", paths.input_dir],
    ["output", paths.output_dir],
  ]
    .filter(([, value]) => value)
    .map(
      ([label, value]) => `
        <div class="path-row">
          <small>${escapeHtml(label)}</small>
          <span>${escapeHtml(value)}</span>
        </div>
      `,
    )
    .join("");
  const diagnosticRows = diagnostics
    .map(
      (item) => `
        <div class="diagnostic-row ${escapeHtml(item.level || "warn")}">
          <strong>${escapeHtml(item.title || "")}</strong>
          <small>${escapeHtml(item.message || "")}</small>
        </div>
      `,
    )
    .join("");
  loadDiagnostics.innerHTML = `
    <div class="path-stack">${pathRows}</div>
    ${diagnosticRows}
  `;
}

function renderModelRecommendations(data) {
  const items = data.recommendations || [];
  if (!items.length) {
    modelRecommendations.innerHTML = `<p class="empty-state">暂无推荐结果。</p>`;
    return;
  }
  modelRecommendations.innerHTML = items
    .map(
      (item) => `
        <article class="model-row ${item.status}">
          <div>
            <strong>${escapeHtml(item.step)}</strong>
            <small>${escapeHtml((item.recommended || []).join(" / "))}</small>
          </div>
          <p>${escapeHtml(item.reason || "")}</p>
          <span>${escapeHtml(labelForStatus(item.status))}</span>
        </article>
      `,
    )
    .join("");
}

function renderDiskPlan(plan) {
  const disk = plan.disk || {};
  if (!disk || disk.ok === undefined) return "";
  const state = disk.ok === false ? "blocked" : disk.ok === true ? "good" : "warn";
  const title = disk.ok === false ? "磁盘空间不足" : disk.ok === true ? "磁盘空间充足" : "磁盘空间待确认";
  const detail =
    disk.ok === null
      ? disk.message || "无法读取磁盘空间。"
      : `目标磁盘剩余 ${formatGb(disk.free_gb)}；预计还需下载 ${formatGb(disk.required_gb)}；建议至少预留 ${formatGb(disk.recommended_free_gb)}。${
          disk.ok === false ? " 可切换到 Wan5B 保守档或仅后期工具。" : ""
        }`;
  return `
    <div class="missing-row ${state}">
      <strong>${escapeHtml(title)}</strong>
      <small>${escapeHtml(detail)}${disk.path ? ` | ${escapeHtml(disk.path)}` : ""}</small>
    </div>
  `;
}

function renderPrerequisites(report) {
  if (!prerequisiteChecks) return;
  if (!report || !Array.isArray(report.checks)) {
    prerequisiteChecks.innerHTML = `<p class="empty-state">暂未取得前置条件检测结果。</p>`;
    return;
  }
  const summaryState = report.ok ? "good" : "blocked";
  const summaryText = report.ok
    ? `可继续。${report.warning_count || 0} 个提示项不阻止安装。`
    : `有 ${report.blocked_count || 0} 个阻断项需要先处理。`;
  const rows = report.checks
    .map((item) => {
      const state = item.status === "ok" ? "good" : item.status === "blocked" ? "blocked" : "warn";
      const action = item.action ? `<small class="next-action">${escapeHtml(item.action)}</small>` : "";
      return `
        <div class="missing-row ${state}">
          <strong>${escapeHtml(item.label || item.id || "")}</strong>
          <small>${escapeHtml(item.message || "")}</small>
          ${action}
        </div>
      `;
    })
    .join("");
  prerequisiteChecks.innerHTML = `
    <div class="missing-row ${summaryState}">
      <strong>${report.ok ? "基础环境可继续" : "基础环境需要处理"}</strong>
      <small>${escapeHtml(summaryText)}</small>
    </div>
    ${rows}
  `;
}

function installProfileOptions() {
  return (
    environmentData?.install_profiles || [
      { id: "auto", label: "自动推荐", description: "按当前硬件自动选择。" },
      { id: "cuda-wan5b", label: "CUDA Wan5B 保守档", description: "先跑通 TI2V-5B 和后期。" },
      { id: "cuda-full", label: "CUDA 完整 Wan2.2 档", description: "下载完整 A14B/Wan5B 主线。" },
      { id: "post-only", label: "仅后期工具", description: "只装 RIFE/超分和示例图。" },
    ]
  );
}

function installProfileLabel(profile) {
  const option = installProfileOptions().find((item) => item.id === profile);
  return option?.label || profile || "auto";
}

function hostnameFromUrl(value) {
  try {
    return value ? new URL(value).hostname || "" : "";
  } catch {
    return "";
  }
}

function activeDownloadSettings() {
  return bootstrapData?.download_settings || environmentData?.download_settings || {};
}

function friendlyDownloadHostLabel(host) {
  const settings = activeDownloadSettings();
  if (host === "huggingface.co") {
    const mirrorHost = hostnameFromUrl(settings.hf_endpoint || settings.saved_hf_endpoint);
    return mirrorHost && mirrorHost !== host ? `Hugging Face 镜像 ${mirrorHost}` : "Hugging Face";
  }
  if (host === "pypi.org") {
    const pipHost = hostnameFromUrl(settings.pip_index_url || settings.saved_pip_index_url);
    return pipHost && pipHost !== host ? `pip 镜像 ${pipHost}` : "PyPI";
  }
  if (host === "github.com") return "GitHub";
  if (host === "download.pytorch.org") return "PyTorch 下载源";
  return host || "未知下载源";
}

function workflowAssetDownloadHosts() {
  const hosts = new Set();
  const missing = environmentData?.missing_installable || [];
  for (const item of missing) {
    if (item.local_available) continue;
    if (item.download_host) hosts.add(item.download_host);
  }
  const selectedHasCustomNodes = (environmentData?.install_plan?.items || []).some((item) => item.item_type === "custom_node");
  if (missing.length && selectedHasCustomNodes && bootstrapData?.venv_ready) {
    hosts.add("pypi.org");
  }
  return hosts;
}

function fullSetupDownloadHosts() {
  const hosts = workflowAssetDownloadHosts();
  if (!bootstrapData?.comfy_connected) {
    hosts.add("github.com");
    hosts.add("pypi.org");
    const backend = String(bootstrapData?.install_disk?.backend || "").toLowerCase();
    if (backend === "cuda" || backend === "cpu") hosts.add("download.pytorch.org");
  }
  return hosts;
}

function downloadHostListText(hosts) {
  const labels = [...hosts].map(friendlyDownloadHostLabel).filter(Boolean);
  return labels.length ? labels.join("、") : "无需外网下载源";
}

function renderInstallProfileSelector(data) {
  if (!installProfileSelector) return;
  const options = data.install_profiles || installProfileOptions();
  const values = options.map((item) => item.id);
  if (!values.includes(selectedInstallProfile)) selectedInstallProfile = "auto";
  installProfileSelector.innerHTML = options
    .map((item) => {
      const effective = item.id === "auto" ? ` -> ${item.effective_profile || data.install_profile || "auto"}` : "";
      const marker = item.recommended && item.id !== "auto" ? " · 推荐" : "";
      return `<option value="${escapeHtml(item.id)}">${escapeHtml(item.label || item.id)}${escapeHtml(effective)}${marker}</option>`;
    })
    .join("");
  installProfileSelector.value = selectedInstallProfile;
  const selected = options.find((item) => item.id === selectedInstallProfile) || options[0];
  installProfileSelector.title = selected?.description || "";
}

function renderMissingItems(data) {
  const items = data.missing_installable || [];
  const plan = data.install_plan || {};
  const diskBlocked = plan.disk?.ok === false;
  renderInstallProfileSelector(data);
  installMissingButton.disabled = !items.length || diskBlocked;
  if (installMissingButton.disabled) {
    setWorkflowAssetInstallConfirming(false);
  } else {
    installMissingButton.textContent = allowWorkflowAssetInstall ? workflowAssetConfirmText : workflowAssetButtonText;
  }
  if (!items.length) {
    missingItems.innerHTML =
      renderDiskPlan(plan) +
      `<p class="empty-state good">没有可一键安装的缺失项。当前档位：${escapeHtml(installProfileLabel(selectedInstallProfile))}。</p>`;
    return;
  }
  const planSummary = `
    <div class="missing-row plan-row">
      <strong>${escapeHtml(installProfileLabel(selectedInstallProfile))} 安装计划</strong>
      <small>本档位共 ${plan.asset_count || 0} 个模型/权重、${plan.custom_node_count || 0} 个节点；缺失约 ${formatGb(plan.missing_expected_gb || 0)}，完整档位约 ${formatGb(plan.total_expected_gb || 0)}。</small>
    </div>
  `;
  missingItems.innerHTML = planSummary + renderDiskPlan(plan) + items
    .map(
      (item) => {
        const resumeInfo = item.partial_exists
          ? ` | 已下载 ${formatGb(item.partial_gb || 0)}，剩余 ${formatGb(item.remaining_gb || 0)}，可断点续传`
          : "";
        const localInfo = item.local_available
          ? ` | 本地缓存可用，点击安装会复用：${item.local_path || ""}`
          : "";
        const sizeInfo = item.expected_gb ? ` | 完整大小 ${formatGb(item.expected_gb)}` : "";
        return `
          <div class="missing-row">
            <strong>${escapeHtml(item.label || item.id)}</strong>
            <small>${escapeHtml(item.path || "")}${escapeHtml(sizeInfo + resumeInfo + localInfo)}</small>
          </div>
        `;
      },
    )
    .join("");
}

function updateFullSetupAvailability(data = environmentData) {
  if (!runFullSetupButton) return;
  const diskBlocked = data?.install_plan?.disk?.ok === false;
  runFullSetupButton.disabled = Boolean(diskBlocked);
  runFullSetupButton.title = diskBlocked
    ? "当前安装档位磁盘空间不足，请降档或清理磁盘。"
    : "自动安装/更新 ComfyUI，并安装当前档位模型、节点和后期权重。";
  if (diskBlocked) setFullSetupConfirming(false);
}

async function loadInstallPlan(profile = selectedInstallProfile) {
  if (!environmentData) return;
  selectedInstallProfile = profile || "auto";
  const requestId = ++installPlanRequestId;
  setFullSetupConfirming(false);
  setWorkflowAssetInstallConfirming(false);
  if (installMissingButton) installMissingButton.disabled = true;
  if (runFullSetupButton) {
    runFullSetupButton.disabled = true;
    runFullSetupButton.title = "正在刷新当前档位安装计划。";
  }
  if (missingItems) {
    missingItems.innerHTML = `<div class="missing-row plan-row"><strong>正在刷新安装计划</strong><small>${escapeHtml(installProfileLabel(selectedInstallProfile))}</small></div>`;
  }
  try {
    const response = await fetch(`/api/install-plan?profile=${encodeURIComponent(selectedInstallProfile)}`);
    const data = await response.json();
    if (!response.ok) throw new Error(formatError(data.detail || data));
    if (requestId !== installPlanRequestId) return;
    environmentData = {
      ...environmentData,
      install_profile: data.profile,
      requested_install_profile: data.requested_profile,
      recommended_install_profile: data.recommended_profile,
      install_profiles: data.install_profiles || environmentData.install_profiles,
      install_plan: data.install_plan,
      missing_installable: data.missing_installable,
      base_dir: data.base_dir || environmentData.base_dir,
      paths: data.paths || environmentData.paths,
    };
    renderMissingItems(environmentData);
    updateFullSetupAvailability(environmentData);
  } catch (error) {
    if (requestId !== installPlanRequestId) return;
    if (missingItems) {
      missingItems.innerHTML = `<div class="missing-row blocked"><strong>安装计划刷新失败</strong><small>${escapeHtml(error.message)}</small></div>`;
    }
    if (runFullSetupButton) {
      runFullSetupButton.disabled = true;
      runFullSetupButton.title = "安装计划刷新失败，请重新侦测后再一键准备。";
    }
    appendLog(`安装计划刷新失败：${error.message}`);
  }
}

function renderBootstrap(data) {
  bootstrapData = data;
  if (!bootstrapCards) return;
  renderPrerequisites(data.prerequisites);
  renderDownloadSettings(data.download_settings);
  const installDisk = data.install_disk || {};
  const comfyUrlMode = data.comfy_url_configured ? "手动配置" : "自动检测";
  const comfyUrlDetail = data.comfy_connected
    ? `${data.comfy_url} | ${comfyUrlMode}`
    : `${data.comfy_error || data.comfy_url} | 已尝试 ${(data.comfy_candidate_urls || []).join(" / ")}`;
  const diskValue =
    installDisk.ok === false ? "空间不足" : installDisk.ok === true ? "空间充足" : "待确认";
  const diskDetail =
    installDisk.ok === null || installDisk.ok === undefined
      ? installDisk.message || data.install_dir || "-"
      : `后端 ${installDisk.backend || "auto"}；剩余 ${formatGb(installDisk.free_gb)}，建议预留 ${formatGb(installDisk.recommended_free_gb)} | ${installDisk.path || data.install_dir || "-"}`;
  const runtimeLabel = data.runtime_label || "ComfyUI runtime";
  const runtimeReady = Boolean(data.runtime_source_ready);
  const runtimeDetail = data.bundled_main
    ? `${data.bundled_main} | ${data.venv_python || "-"}`
    : data.comfy_main || data.install_dir || "-";
  const cards = [
    {
      label: "ComfyUI 连接",
      value: data.comfy_connected ? "已连接" : "未连接",
      detail: comfyUrlDetail,
      state: data.comfy_connected ? "ok" : "blocked",
    },
    {
      label: "ComfyUI runtime",
      value: runtimeReady ? "可启动" : data.desktop_available ? "Desktop 可启动" : "未就绪",
      detail: `${runtimeLabel} | ${runtimeDetail}`,
      state: runtimeReady ? "ok" : data.desktop_available ? "warn" : "blocked",
    },
    {
      label: "当前工作目录",
      value: data.paths?.base_dir_mismatch ? "运行目录不同" : "已确认",
      detail: data.active_base_dir || data.base_dir || "-",
      state: data.paths?.base_dir_mismatch ? "warn" : "ok",
    },
    {
      label: "运行环境",
      value: data.venv_ready ? "venv 就绪" : "venv 未就绪",
      detail: data.venv_python || "-",
      state: data.venv_ready ? "ok" : "warn",
    },
    {
      label: "Git",
      value: data.git_ready ? "可用" : "未检测到",
      detail: data.git_ready ? "可 clone/update ComfyUI" : "会改用 GitHub ZIP 下载源码",
      state: data.git_ready ? "ok" : "warn",
    },
    {
      label: "安装磁盘",
      value: diskValue,
      detail: diskDetail,
      state: installDisk.ok === false ? "blocked" : installDisk.ok === true ? "ok" : "warn",
    },
  ];
  bootstrapCards.innerHTML = cards
    .map(
      (card) => `
        <div class="env-card ${card.state}">
          <small>${escapeHtml(card.label)}</small>
          <strong>${escapeHtml(card.value)}</strong>
          <span>${escapeHtml(card.detail)}</span>
        </div>
      `,
    )
    .join("");
  installComfyButton.disabled = !data.can_install_comfyui || installDisk.ok === false;
  if (installComfyButton.disabled) {
    setComfyInstallConfirming(false);
  } else {
    installComfyButton.textContent = allowComfyInstall ? comfyInstallConfirmText : comfyInstallButtonText;
  }
  if (data.comfy_connected) {
    startComfyButton.disabled = true;
    startComfyButton.textContent = "ComfyUI 已运行";
  } else {
    startComfyButton.disabled = !data.can_start_comfyui;
    startComfyButton.textContent = data.can_start_comfyui ? "启动 ComfyUI" : "先安装/选择 ComfyUI";
  }
}

async function loadBootstrap() {
  if (!bootstrapCards) return;
  refreshBootstrapButton.disabled = true;
  try {
    const response = await fetch("/api/bootstrap");
    const data = await response.json();
    if (!response.ok) throw new Error(formatError(data.detail || data));
    renderBootstrap(data);
  } catch (error) {
    bootstrapCards.innerHTML = `<div class="env-card blocked"><small>ComfyUI 安装状态</small><strong>检测失败</strong><span>${escapeHtml(error.message)}</span></div>`;
    if (prerequisiteChecks) {
      prerequisiteChecks.innerHTML = `<div class="missing-row blocked"><strong>前置条件检测失败</strong><small>${escapeHtml(error.message)}</small></div>`;
    }
    appendLog(`ComfyUI 安装状态检测失败：${error.message}`);
  } finally {
    refreshBootstrapButton.disabled = false;
  }
}

async function startBootstrapJob(url, button, confirmText) {
  if (confirmText && !allowComfyInstall) {
    setComfyInstallConfirming(true);
    setLog(`${confirmText} 确认继续请再点击一次“${comfyInstallConfirmText}”。`);
    return;
  }
  setComfyInstallConfirming(false);
  button.disabled = true;
  setLog("正在启动后台任务。");
  try {
    const response = await fetch(url, { method: "POST" });
    const job = await response.json();
    if (!response.ok) throw new Error(formatError(job.detail || job));
    pollBootstrapJob(job.id);
    if (bootstrapPollTimer) clearInterval(bootstrapPollTimer);
    bootstrapPollTimer = window.setInterval(() => pollBootstrapJob(job.id), 2500);
  } catch (error) {
    appendLog(`后台任务启动失败：${error.message}`);
    button.disabled = false;
    if (button === installComfyButton) {
      setComfyInstallConfirming(false);
    }
  }
}

async function pollBootstrapJob(jobId) {
  try {
    const response = await fetch(`/api/install/${jobId}`);
    const job = await response.json();
    if (!response.ok) throw new Error(formatError(job.detail || job));
    setLog((job.log || []).join("\n"));
    await loadBootstrap();
    if (job.completed || job.status === "success" || job.status === "failed" || job.status === "stopped") {
      if (bootstrapPollTimer) {
        clearInterval(bootstrapPollTimer);
        bootstrapPollTimer = null;
      }
      installComfyButton.disabled = !(
        bootstrapData &&
        bootstrapData.can_install_comfyui &&
        bootstrapData.install_disk?.ok !== false
      );
      if (bootstrapData?.comfy_connected) {
        startComfyButton.disabled = true;
        startComfyButton.textContent = "ComfyUI 已运行";
      } else {
        startComfyButton.disabled = !(bootstrapData && bootstrapData.can_start_comfyui);
        startComfyButton.textContent = bootstrapData?.can_start_comfyui ? "启动 ComfyUI" : "先安装 ComfyUI";
      }
      if (job.status === "failed") {
        appendLog(formatJobFailureHint(job));
      }
      await loadEnvironment();
    }
  } catch (error) {
    if (bootstrapPollTimer) {
      clearInterval(bootstrapPollTimer);
      bootstrapPollTimer = null;
    }
    appendLog(`后台任务轮询失败：${error.message}`);
    await loadBootstrap();
  }
}

async function startFullSetup() {
  if (!runFullSetupButton) return;
  if (!allowFullSetup) {
    setFullSetupConfirming(true);
    const setupScope = bootstrapData?.comfy_connected
      ? "已检测到 ComfyUI 正在运行，本次会跳过源码版 ComfyUI 安装/更新，只把当前档位模型、节点和后期权重安装到运行中的 active base。"
      : "一键准备会安装/更新 ComfyUI，并安装当前档位模型、节点和后期权重。";
    const sourceText = downloadHostListText(fullSetupDownloadHosts());
    setLog(
      `${setupScope} 档位：${installProfileLabel(selectedInstallProfile)}。开始前会先检查本次需要的下载源：${sourceText}；首次运行可能下载数 GB 到数十 GB。确认继续请再点击一次“${fullSetupConfirmText}”。`,
    );
    return;
  }
  setFullSetupConfirming(false);
  runFullSetupButton.disabled = true;
  detectEnvironmentButton.disabled = true;
  installMissingButton.disabled = true;
  setLog("正在启动一键准备环境任务。");
  try {
    const profile = encodeURIComponent(selectedInstallProfile || "auto");
    const response = await fetch(`/api/bootstrap/full-setup?backend=auto&profile=${profile}`, { method: "POST" });
    const job = await response.json();
    if (!response.ok) throw new Error(formatError(job.detail || job));
    await pollFullSetupJob(job.id);
    if (fullSetupPollTimer) clearInterval(fullSetupPollTimer);
    fullSetupPollTimer = window.setInterval(() => pollFullSetupJob(job.id), 2500);
  } catch (error) {
    appendLog(`一键准备环境启动失败：${error.message}`);
    runFullSetupButton.disabled = false;
    detectEnvironmentButton.disabled = false;
    await loadBootstrap();
    await loadEnvironment();
  }
}

async function pollFullSetupJob(jobId) {
  try {
    const response = await fetch(`/api/install/${jobId}`);
    const job = await response.json();
    if (!response.ok) throw new Error(formatError(job.detail || job));
    setLog((job.log || []).join("\n"));
    await loadBootstrap();
    if (job.completed || job.status === "success" || job.status === "failed" || job.status === "stopped") {
      if (fullSetupPollTimer) {
        clearInterval(fullSetupPollTimer);
        fullSetupPollTimer = null;
      }
      runFullSetupButton.disabled = false;
      detectEnvironmentButton.disabled = false;
      await loadEnvironment();
      if (job.status === "success") {
        appendLog(job.dry_run ? "一键准备 dry-run 通过。" : "一键准备完成。建议点击“生成链路测试”验证真实出片。");
      } else {
        appendLog(formatJobFailureHint(job));
      }
    }
  } catch (error) {
    if (fullSetupPollTimer) {
      clearInterval(fullSetupPollTimer);
      fullSetupPollTimer = null;
    }
    runFullSetupButton.disabled = false;
    detectEnvironmentButton.disabled = false;
    appendLog(`一键准备环境轮询失败：${error.message}`);
    await loadBootstrap();
    await loadEnvironment();
  }
}

function formatJobFailureHint(job) {
  const hint = job?.failure_hint;
  if (!hint) return "任务失败。请点击“复制诊断信息”，把诊断 JSON 发给维护者。";
  const actions = (hint.actions || []).map((item, index) => `${index + 1}. ${item}`).join("\n");
  return [
    "",
    `失败原因：${hint.title || "安装任务失败"}`,
    hint.message || "",
    actions ? `下一步：\n${actions}` : "",
  ]
    .filter(Boolean)
    .join("\n");
}

function renderSmallModelRoutes(data) {
  const routes = data.model_options?.small_model_routes || [];
  if (!smallModelRoutes) return;
  if (!routes.length) {
    smallModelRoutes.innerHTML = `<p class="empty-state">暂无低配路线建议。</p>`;
    return;
  }
  smallModelRoutes.innerHTML = routes
    .map(
      (item) => `
        <div class="route-row">
          <strong>${escapeHtml(item.hardware || "")}</strong>
          <small>${escapeHtml(item.route || "")}</small>
        </div>
      `,
    )
    .join("");
}

function applyRecommendedModelSelections(data) {
  const recommended = data.model_options?.recommended || {};
  Object.entries(recommended).forEach(([stepId, modelId]) => {
    if (!selectedModels[stepId]) selectedModels[stepId] = modelId;
  });
  if (currentStep().id !== "environment") {
    renderModelSelector();
  }
}

function renderEnvironment(data) {
  environmentData = data;
  applyRecommendedModelSelections(data);
  renderDownloadSettings(data.download_settings);
  const missingCount = (data.missing_installable || []).length;
  const blockedCount = (data.blocked || []).length;
  environmentSummary.textContent = data.ok
    ? "环境通过，可以继续关键帧。"
    : missingCount
      ? `检测到 ${missingCount} 个可一键安装的缺失项。`
      : blockedCount
        ? `还有 ${blockedCount} 个核心步骤需要处理。`
        : "环境有警告，请查看下方建议。";
  renderEnvironmentCards(data);
  renderModelRecommendations(data);
  renderLoadDiagnostics(data);
  renderMissingItems(data);
  renderSmallModelRoutes(data);
  updateFullSetupAvailability(data);
  if (runVideoSmokeButton) {
    const canSmokeTest = Boolean(data.comfy?.connected) && !missingCount && !blockedCount;
    runVideoSmokeButton.disabled = !canSmokeTest;
    runVideoSmokeButton.title = canSmokeTest
      ? "提交一个最保守的短视频任务，验证模型、节点、队列和输出。"
      : "请先连接 ComfyUI，并处理缺失项/阻断项。";
  }
}

async function loadEnvironment() {
  if (!environmentCards) return;
  setFullSetupConfirming(false);
  setWorkflowAssetInstallConfirming(false);
  detectEnvironmentButton.disabled = true;
  installMissingButton.disabled = true;
  if (runFullSetupButton) runFullSetupButton.disabled = true;
  if (runVideoSmokeButton) runVideoSmokeButton.disabled = true;
  environmentSummary.textContent = "正在检测系统、GPU、ComfyUI、ffmpeg 和模型文件。";
  try {
    const response = await fetch("/api/environment");
    const data = await response.json();
    if (!response.ok) throw new Error(formatError(data.detail || data));
    renderEnvironment(data);
    if (selectedInstallProfile !== "auto") {
      await loadInstallPlan(selectedInstallProfile);
    }
    const comfy = data.comfy || {};
    if (comfy.connected) {
      setStatus(true, `${comfy.comfyui_version || "ComfyUI"} | ${data.hardware?.max_vram_gb || "-"} GB 单卡`);
    } else {
      setStatus(false, "ComfyUI 未连接");
    }
    setLog(data.ok ? "环境侦测通过，可以进入关键帧。" : "环境侦测完成，请查看缺失项和模型建议。");
  } catch (error) {
    environmentSummary.textContent = "环境侦测失败。";
    environmentCards.innerHTML = `<div class="env-card blocked"><small>错误</small><strong>侦测失败</strong><span>${escapeHtml(error.message)}</span></div>`;
    modelRecommendations.innerHTML = "";
    if (loadDiagnostics) loadDiagnostics.innerHTML = "";
    missingItems.innerHTML = "";
    if (smallModelRoutes) smallModelRoutes.innerHTML = "";
    setLog(`环境侦测失败：${error.message}`);
    if (runFullSetupButton) runFullSetupButton.disabled = false;
    if (runVideoSmokeButton) runVideoSmokeButton.disabled = true;
  } finally {
    detectEnvironmentButton.disabled = false;
    if (environmentData) {
      installMissingButton.disabled =
        !(environmentData.missing_installable || []).length || environmentData.install_plan?.disk?.ok === false;
    }
  }
}

function formatSelfTestSummary(job) {
  const selfTest = job?.self_test || {};
  const results = selfTest.results || [];
  if (!results.length) {
    return job.status === "success" ? "本机自检通过。" : formatJobFailureHint(job);
  }
  const lines = results.map((item) => {
    const name = (item.command || "").includes("prerequisite_doctor.py") ? "前置条件" : (item.command || "").includes("self_check.py") ? "项目自检" : "检查";
    return `${name}：${item.ok ? "通过" : "未通过"}（退出码 ${item.return_code}）`;
  });
  return [`本机自检${selfTest.ok ? "通过" : "发现问题"}。`, ...lines, selfTest.ok ? "" : "请点击“复制诊断信息”，把 JSON 发给维护者。"].filter(Boolean).join("\n");
}

async function startSelfTest() {
  if (!runSelfTestButton) return;
  runSelfTestButton.disabled = true;
  setLog("正在启动本机自检。该检查不会下载模型，也不会启动真实生成。");
  try {
    const response = await fetch("/api/self-test", { method: "POST" });
    const job = await response.json();
    if (!response.ok) throw new Error(formatError(job.detail || job));
    pollSelfTest(job.id);
    if (selfTestPollTimer) clearInterval(selfTestPollTimer);
    selfTestPollTimer = window.setInterval(() => pollSelfTest(job.id), 1800);
  } catch (error) {
    runSelfTestButton.disabled = false;
    appendLog(`本机自检启动失败：${error.message}`);
  }
}

async function pollSelfTest(jobId) {
  try {
    const response = await fetch(`/api/install/${jobId}`);
    const job = await response.json();
    if (!response.ok) throw new Error(formatError(job.detail || job));
    setLog((job.log || []).join("\n"));
    if (job.completed) {
      if (selfTestPollTimer) {
        clearInterval(selfTestPollTimer);
        selfTestPollTimer = null;
      }
      runSelfTestButton.disabled = false;
      appendLog(formatSelfTestSummary(job));
    }
  } catch (error) {
    if (selfTestPollTimer) {
      clearInterval(selfTestPollTimer);
      selfTestPollTimer = null;
    }
    runSelfTestButton.disabled = false;
    appendLog(`本机自检轮询失败：${error.message}`);
  }
}

function smokeStatusLabel(status) {
  if (status === "success") return "生成链路测试通过";
  if (status === "running") return "生成链路测试运行中";
  if (status === "queued") return "生成链路测试排队中";
  return status || "生成链路测试";
}

async function startVideoSmokeTest() {
  if (!runVideoSmokeButton) return;
  if (videoSmokePollTimer) {
    clearInterval(videoSmokePollTimer);
    videoSmokePollTimer = null;
  }
  runVideoSmokeButton.disabled = true;
  continueButton.classList.add("hidden");
  repeatButton.classList.add("hidden");
  jobStatus.textContent = "提交生成链路测试";
  resultHint.textContent = "正在提交一个低成本短视频，用来验证 ComfyUI 真实生成链路。";
  resultView.innerHTML = "<p>任务提交后，生成结果会显示在这里。</p>";
  setProgress("queued");
  setLog("正在提交生成链路测试。它会使用当前环境可用的最保守视频模型。");
  try {
    const response = await fetch("/api/video-smoke-test", { method: "POST" });
    const job = await response.json();
    if (!response.ok) throw new Error(formatError(job.detail || job));
    activeJobId = job.id;
    setJobMeta(job);
    appendLog(`已提交生成链路测试：${job.title || job.prompt_id || job.id}`);
    await pollVideoSmokeJob(job.id);
    videoSmokePollTimer = window.setInterval(() => pollVideoSmokeJob(job.id), 2500);
  } catch (error) {
    setProgress("failed");
    jobStatus.textContent = "生成链路测试失败";
    appendLog(`生成链路测试启动失败：${error.message}`);
    runVideoSmokeButton.disabled = false;
  }
}

async function pollVideoSmokeJob(jobId) {
  try {
    const response = await fetch(`/api/jobs/${jobId}`);
    const job = await response.json();
    if (!response.ok) throw new Error(formatError(job.detail || job));

    setJobMeta(job);
    setProgress(job.status);
    jobStatus.textContent = smokeStatusLabel(job.status);
    if (job.media && job.media.length) {
      renderMedia(job.media);
    }

    if (job.status === "success") {
      if (videoSmokePollTimer) {
        clearInterval(videoSmokePollTimer);
        videoSmokePollTimer = null;
      }
      runVideoSmokeButton.disabled = false;
      resultHint.textContent = "生成链路测试通过。现在可以按左侧流程从关键帧进入正式分镜生成。";
      appendLog("生成链路测试通过：ComfyUI 队列、模型、节点和输出目录都完成了一次真实视频生成。");
      return;
    }

    if (job.completed || terminalJobFailed(job) || (job.status && !["queued", "running"].includes(job.status))) {
      if (videoSmokePollTimer) {
        clearInterval(videoSmokePollTimer);
        videoSmokePollTimer = null;
      }
      runVideoSmokeButton.disabled = false;
      jobStatus.textContent = terminalJobFailed(job) ? "生成链路测试失败" : smokeStatusLabel(job.status);
      setProgress("failed");
      const detail = formatJobMessages(job);
      appendLog(detail ? `生成链路测试失败：${job.status}\n${detail}` : `生成链路测试状态：${job.status}`);
    }
  } catch (error) {
    if (videoSmokePollTimer) {
      clearInterval(videoSmokePollTimer);
      videoSmokePollTimer = null;
    }
    runVideoSmokeButton.disabled = false;
    jobStatus.textContent = "生成链路测试轮询失败";
    setProgress("failed");
    appendLog(`生成链路测试轮询失败：${error.message}`);
  }
}

async function startInstall() {
  const missing = environmentData?.missing_installable || [];
  const plan = environmentData?.install_plan || {};
  if (!missing.length) {
    setWorkflowAssetInstallConfirming(false);
    setLog("没有需要一键安装的缺失项。");
    return;
  }
  if (plan.disk?.ok === false) {
    setWorkflowAssetInstallConfirming(false);
    setLog(
      `磁盘空间不足，已取消安装。当前剩余 ${formatGb(plan.disk.free_gb)}，预计还需下载 ${formatGb(plan.disk.required_gb)}，建议至少预留 ${formatGb(plan.disk.recommended_free_gb)}。可以切换到 Wan5B 保守档或仅后期工具。`,
    );
    return;
  }
  if (!allowWorkflowAssetInstall) {
    setWorkflowAssetInstallConfirming(true);
    const sourceText = downloadHostListText(workflowAssetDownloadHosts());
    setLog(
      `将按 ${installProfileLabel(selectedInstallProfile)} 安装或修复 ${missing.length} 个项目，预计缺失下载约 ${formatGb(plan.missing_expected_gb || 0)}，建议预留 ${formatGb(plan.disk?.recommended_free_gb)}。开始前会先检查本次需要的下载源：${sourceText}。若只是加载异常，会重新校验文件并安装节点依赖。完成后需要重启 ComfyUI。确认继续请再点击一次“${workflowAssetConfirmText}”。`,
    );
    return;
  }
  setWorkflowAssetInstallConfirming(false);

  installMissingButton.disabled = true;
  detectEnvironmentButton.disabled = true;
  setLog("正在启动安装任务。");
  try {
    const profile = encodeURIComponent(selectedInstallProfile || "auto");
    const response = await fetch(`/api/install?profile=${profile}`, { method: "POST" });
    const job = await response.json();
    if (!response.ok) throw new Error(formatError(job.detail || job));
    pollInstall(job.id);
    installPollTimer = window.setInterval(() => pollInstall(job.id), 2500);
  } catch (error) {
    setLog(`安装任务启动失败：${error.message}`);
    installMissingButton.disabled = false;
    detectEnvironmentButton.disabled = false;
    setWorkflowAssetInstallConfirming(false);
  }
}

async function pollInstall(jobId) {
  try {
    const response = await fetch(`/api/install/${jobId}`);
    const job = await response.json();
    if (!response.ok) throw new Error(formatError(job.detail || job));
    setLog((job.log || []).join("\n"));
    if (job.completed) {
      if (installPollTimer) {
        clearInterval(installPollTimer);
        installPollTimer = null;
      }
      detectEnvironmentButton.disabled = false;
      await loadEnvironment();
      appendLog(job.status === "success" ? "安装任务完成。" : formatJobFailureHint(job));
    }
  } catch (error) {
    if (installPollTimer) {
      clearInterval(installPollTimer);
      installPollTimer = null;
    }
    detectEnvironmentButton.disabled = false;
    installMissingButton.disabled = false;
    appendLog(`安装轮询失败：${error.message}`);
  }
}

function renderMedia(media) {
  if (!media || media.length === 0) {
    resultView.innerHTML = "<p>当前步骤完成后，结果会显示在这里。</p>";
    return;
  }
  const first = media[0];
  const firstUrl = mediaUrl(first.url);
  const preview =
    first.kind === "video"
      ? `<video src="${escapeHtml(firstUrl)}" controls playsinline></video>`
      : `<img src="${escapeHtml(firstUrl)}" alt="生成结果" />`;
  const links = media
    .map((item, index) => {
      const label = item.kind === "video" ? `视频 ${index + 1}` : `图片 ${index + 1}`;
      return `<a href="${escapeHtml(mediaUrl(item.url))}" target="_blank" rel="noreferrer">${label}</a>`;
    })
    .join("");
  resultView.innerHTML = `${preview}<div class="result-links">${links}</div>`;
}

function pickVideo(media) {
  return (media || []).find((item) => item.kind === "video") || null;
}

function readProjectResolution() {
  const width = Number(widthInput.value) || 1280;
  const height = Number(heightInput.value) || 704;
  return { width, height };
}

function getUpscaleSourceResolution() {
  return currentVideoResolution || readProjectResolution();
}

function updateTargetResolution() {
  if (!sourceResolutionText || !targetResolutionText || !targetResolutionNote) return;

  const source = getUpscaleSourceResolution();
  const scale = getUpscaleScale();
  const target = {
    width: source.width * scale,
    height: source.height * scale,
  };

  sourceResolutionText.textContent = `${source.width} x ${source.height}`;
  if (targetResolutionLabel) targetResolutionLabel.textContent = `${scale}x 输出目标`;
  targetResolutionText.textContent = `${target.width} x ${target.height}`;

  if (currentVideoResolution) {
    targetResolutionNote.textContent =
      `目标分辨率按上一阶段视频尺寸计算。手动上传视频时，会按上传视频的实际尺寸做 ${scale}x 超分。`;
  } else {
    targetResolutionNote.textContent =
      `还没有上一阶段视频时，目标分辨率按当前项目尺寸预估；手动上传视频会按实际尺寸做 ${scale}x 超分。`;
  }
}

function updateVideoSource() {
  let source = null;
  if (currentStep().id === "deflicker") {
    source = lastFinalVideo || lastDraftVideo;
  } else if (currentStep().id === "rife") {
    source = lastDeflickerVideo || lastFinalVideo || lastDraftVideo;
    if (source) fpsInput.value = currentVideoFps || Number(fpsInput.value || 24);
  } else if (currentStep().id === "upscale") {
    source = lastRifeVideo || lastDeflickerVideo || lastFinalVideo || lastDraftVideo;
    if (source) fpsInput.value = currentVideoFps || Number(fpsInput.value || 24);
  }

  if (!source) {
    sourceVideoFilename.value = "";
    sourceVideoSubfolder.value = "";
    sourceVideoType.value = "";
    updateTargetResolution();
    return;
  }
  sourceVideoFilename.value = source.filename || "";
  sourceVideoSubfolder.value = source.subfolder || "";
  sourceVideoType.value = source.type || "output";
  updateTargetResolution();
}

function goNext() {
  if (activeIndex < steps.length - 1) {
    setStep(steps[activeIndex + 1].id);
  }
}

function goBack() {
  if (activeIndex > 0) {
    setStep(steps[activeIndex - 1].id);
  }
}

async function refreshStatus() {
  try {
    const response = await fetch("/api/status");
    if (!response.ok) throw new Error(await response.text());
    const data = await response.json();
    if (data.connected === false) {
      setStatus(false, "ComfyUI 未连接");
    } else {
      setStatus(true, `${data.comfyui_version} | 空闲显存 ${data.vram_free_gb} GB`);
    }
  } catch (error) {
    setStatus(false, "ComfyUI 未连接");
  }
}

async function validateSetup() {
  validateButton.disabled = true;
  setLog("正在检查 ComfyUI 节点、模型文件和工作流图。");
  try {
    const response = await fetch("/api/validate");
    const data = await response.json();
    if (!response.ok) throw new Error(JSON.stringify(data, null, 2));
    const missingNodes = data.nodes.filter((item) => !item.ok).map((item) => item.name);
    const missingFiles = data.files.filter((item) => !item.ok).map((item) => item.path);
    const brokenGraphs = data.graphs.filter((item) => !item.ok).map((item) => item.name);
    const missingTools = (data.tools || []).filter((item) => !item.ok && item.required !== false).map((item) => item.name);
    if (data.ok) {
      setLog("检查通过：节点、模型文件、输出节点和 API 图都已就绪。");
    } else {
      setLog(
        [
          "检查未通过：",
          missingNodes.length ? `缺少节点：${missingNodes.join(", ")}` : "",
          missingFiles.length ? `缺少文件：\n${missingFiles.join("\n")}` : "",
          brokenGraphs.length ? `工作流图异常：${brokenGraphs.join(", ")}` : "",
          missingTools.length ? `缺少本地工具：${missingTools.join(", ")}` : "",
        ]
          .filter(Boolean)
          .join("\n"),
      );
    }
  } catch (error) {
    setLog(`检查失败：${error.message}`);
  } finally {
    validateButton.disabled = false;
  }
}

async function handlePrimary() {
  const step = currentStep();
  if (step.id === "environment") {
    if (!environmentData) {
      await loadEnvironment();
    }
    completedSteps.add("environment");
    appendLog("进入关键帧。");
    setStep("keyframe");
    return;
  }
  if (step.id === "keyframe") {
    if (!imageInput.files?.length && !allowDefaultKeyframe) {
      allowDefaultKeyframe = true;
      setLog("当前没有上传关键帧。系统会使用内置示例图测试流程；如果要用自己的画面，请先上传图片。确认使用示例图请再点击一次。");
      keyframePreview.innerHTML = "<p>未上传关键帧。再次点击按钮会使用内置电竞房示例图继续。</p>";
      primaryButton.textContent = "继续使用内置示例图";
      return;
    }
    completedSteps.add("keyframe");
    appendLog(`关键帧已确认，进入 ${selectedModelName("draft")}。`);
    setStep("draft");
    return;
  }
  if (step.id === "edit") {
    completedSteps.add("edit");
    updateStepper();
    setLog(`流程完成。输出目录：${outputDirectoryText()}`);
    return;
  }
  await submitJob();
}

async function submitJob() {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }

  const step = currentStep();
  syncSelectedModel({ applyDefaults: false });
  updateVideoSource();
  const workflowReady = await preflightWorkflow({ quiet: false });
  if (!workflowReady) {
    jobStatus.textContent = "workflow 预检失败";
    setProgress("failed");
    repeatButton.classList.remove("hidden");
    return;
  }

  const payload = new FormData(form);
  primaryButton.disabled = true;
  continueButton.classList.add("hidden");
  repeatButton.classList.add("hidden");
  jobStatus.textContent = "提交中";
  resultView.innerHTML = "<p>任务已提交，等待 ComfyUI 接收。</p>";
  setProgress("queued");
  setLog("");

  try {
    const response = await fetch("/api/generate", {
      method: "POST",
      body: payload,
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(formatError(data.detail || data));
    }

    activeJobId = data.id;
    setJobMeta(data);
    if (data.local || data.status === "success") {
      appendLog(data.local ? "本地处理完成。" : "任务已完成。");
      await pollJob(activeJobId, step.id);
      return;
    }
    jobStatus.textContent = "排队中";
    appendLog(`已进入队列：${data.prompt_id || "已提交"}`);
    pollTimer = window.setInterval(() => pollJob(activeJobId, step.id), 2500);
    await pollJob(activeJobId, step.id);
  } catch (error) {
    jobStatus.textContent = "失败";
    setProgress("failed");
    appendLog(error.message);
    primaryButton.disabled = false;
    repeatButton.classList.remove("hidden");
  }
}

function formatError(detail) {
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    if (Array.isArray(detail.errors) || Array.isArray(detail.actions)) {
      const title = detail.message || "请求失败";
      const errors = Array.isArray(detail.errors) ? detail.errors : [];
      const actions = Array.isArray(detail.actions)
        ? ["下一步：", ...detail.actions.map((item, index) => `${index + 1}. ${item}`)]
        : [];
      return [title, ...errors, ...actions].filter(Boolean).join("\n");
    }
    if (detail.message) return detail.message;
  }
  return JSON.stringify(detail, null, 2);
}

async function copyDiagnostics() {
  if (!copyDiagnosticsButton) return;
  copyDiagnosticsButton.disabled = true;
  try {
    const response = await fetch("/api/diagnostics");
    const data = await response.json();
    if (!response.ok) throw new Error(formatError(data.detail || data));
    const text = JSON.stringify(data, null, 2);
    if (navigator.clipboard?.writeText) {
      try {
        await navigator.clipboard.writeText(text);
        appendLog("诊断信息已复制到剪贴板。");
      } catch (clipboardError) {
        setLog(text);
        appendLog(`浏览器阻止剪贴板写入，诊断信息已显示在日志框：${clipboardError.message}`);
      }
    } else {
      setLog(text);
      appendLog("浏览器不允许写入剪贴板，诊断信息已显示在日志框。");
    }
  } catch (error) {
    appendLog(`复制诊断信息失败：${error.message}`);
  } finally {
    copyDiagnosticsButton.disabled = false;
  }
}

function diagnosticsFilename(data = {}) {
  const stamp = String(data.generated_at || new Date().toISOString())
    .replaceAll(":", "-")
    .replaceAll(" ", "_")
    .replaceAll("/", "-");
  return `wan22-diagnostics-${stamp}.json`;
}

async function downloadDiagnostics() {
  if (!downloadDiagnosticsButton) return;
  downloadDiagnosticsButton.disabled = true;
  try {
    const response = await fetch("/api/diagnostics");
    const data = await response.json();
    if (!response.ok) throw new Error(formatError(data.detail || data));
    const text = JSON.stringify(data, null, 2);
    const blob = new Blob([text], { type: "application/json;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = diagnosticsFilename(data);
    document.body.appendChild(link);
    link.click();
    link.remove();
    URL.revokeObjectURL(url);
    appendLog("诊断包已下载。");
  } catch (error) {
    appendLog(`下载诊断包失败：${error.message}`);
  } finally {
    downloadDiagnosticsButton.disabled = false;
  }
}

function terminalJobFailed(job) {
  return ["failed", "error", "lost", "stopped", "canceled", "cancelled"].includes(String(job.status || "").toLowerCase());
}

function formatJobMessages(job) {
  const lines = [];
  if (job.error) lines.push(job.error);
  if (Array.isArray(job.friendly_messages) && job.friendly_messages.length) {
    lines.push(...job.friendly_messages);
  } else if (Array.isArray(job.messages) && job.messages.length) {
    lines.push(...job.messages.slice(-5).map((item) => (typeof item === "string" ? item : JSON.stringify(item))));
  }
  return lines.filter(Boolean).join("\n");
}

async function pollJob(jobId, stepId) {
  try {
    const response = await fetch(`/api/jobs/${jobId}`);
    const job = await response.json();
    if (!response.ok) throw new Error(formatError(job.detail || job));

    setJobMeta(job);
    setProgress(job.status);
    jobStatus.textContent =
      job.status === "success"
        ? "完成"
        : job.status === "running"
          ? "运行中"
          : job.status === "queued"
            ? "排队中"
            : job.status;

    if (job.media && job.media.length) {
      renderMedia(job.media);
    }

    if (job.status === "success") {
      clearInterval(pollTimer);
      pollTimer = null;
      primaryButton.disabled = false;
      completedSteps.add(stepId);
      updateStepper();

      const video = pickVideo(job.media);
      if (stepId === "draft" && video) {
        lastDraftVideo = video;
        currentVideoResolution = { width: job.width, height: job.height };
        currentVideoFps = job.fps || currentVideoFps;
      }
      if (stepId === "final" && video) {
        lastFinalVideo = video;
        currentVideoResolution = { width: job.width, height: job.height };
        currentVideoFps = job.fps || currentVideoFps;
      }
      if (stepId === "deflicker" && video) lastDeflickerVideo = video;
      if (stepId === "rife" && video) {
        lastRifeVideo = video;
        currentVideoFps = (job.fps || 24) * Number(rifeMultiplierInput.value || 2);
      }
      if (stepId === "upscale" && video) {
        lastUpscaleVideo = video;
        const sourceResolution = currentVideoResolution || readProjectResolution();
        const scale = getUpscaleScale();
        currentVideoResolution = {
          width: sourceResolution.width * scale,
          height: sourceResolution.height * scale,
        };
      }
      updateTargetResolution();

      appendLog("任务完成。检查结果满意后点击继续下一步。");
      renderMedia(job.media);
      if (activeIndex < steps.length - 1) {
        continueButton.classList.remove("hidden");
      }
      repeatButton.classList.remove("hidden");
    } else if (job.completed || terminalJobFailed(job) || (job.status && !["queued", "running"].includes(job.status))) {
      clearInterval(pollTimer);
      pollTimer = null;
      primaryButton.disabled = false;
      jobStatus.textContent = terminalJobFailed(job) ? "失败" : job.status;
      setProgress("failed");
      repeatButton.classList.remove("hidden");
      const detail = formatJobMessages(job);
      appendLog(detail ? `任务失败：${job.status}\n${detail}` : `任务状态：${job.status}`);
    }
  } catch (error) {
    clearInterval(pollTimer);
    pollTimer = null;
    primaryButton.disabled = false;
    jobStatus.textContent = "轮询失败";
    repeatButton.classList.remove("hidden");
    appendLog(error.message);
  }
}

function updateKeyframePreview() {
  allowDefaultKeyframe = false;
  updateStepCopy();
  const file = imageInput.files && imageInput.files[0];
  if (!file) {
    keyframePreview.innerHTML = "<p>未上传时会使用内置电竞房示例图。</p>";
    return;
  }
  const url = URL.createObjectURL(file);
  keyframePreview.innerHTML = `<img src="${url}" alt="关键帧预览" />`;
}

function updateUploadedVideoMetadata() {
  const file = videoInput.files && videoInput.files[0];
  if (!file) {
    updateTargetResolution();
    return;
  }
  const url = URL.createObjectURL(file);
  const video = document.createElement("video");
  video.preload = "metadata";
  video.onloadedmetadata = () => {
    if (video.videoWidth && video.videoHeight) {
      currentVideoResolution = { width: video.videoWidth, height: video.videoHeight };
      updateTargetResolution();
      appendLog(`已读取上传视频尺寸：${video.videoWidth} x ${video.videoHeight}。`);
    }
    URL.revokeObjectURL(url);
  };
  video.onerror = () => {
    URL.revokeObjectURL(url);
    updateTargetResolution();
  };
  video.src = url;
}

document.querySelectorAll(".step-button").forEach((button) => {
  button.addEventListener("click", () => setStep(button.dataset.step));
});

document.querySelectorAll("#sizePreset button").forEach((button) => {
  button.addEventListener("click", () => {
    setSize(button.dataset.width, button.dataset.height);
  });
});

document.querySelectorAll("#durationPreset button").forEach((button) => {
  button.addEventListener("click", () => {
    setLength(button.dataset.length);
  });
});

form.addEventListener("submit", (event) => event.preventDefault());
primaryButton.addEventListener("click", handlePrimary);
validateButton.addEventListener("click", validateSetup);
detectEnvironmentButton.addEventListener("click", loadEnvironment);
runFullSetupButton?.addEventListener("click", startFullSetup);
runSelfTestButton?.addEventListener("click", startSelfTest);
runVideoSmokeButton?.addEventListener("click", startVideoSmokeTest);
installMissingButton.addEventListener("click", startInstall);
saveDownloadSettingsButton?.addEventListener("click", saveDownloadSettings);
testDownloadSourcesButton?.addEventListener("click", testDownloadSources);
installProfileSelector?.addEventListener("change", () => {
  loadInstallPlan(installProfileSelector.value || "auto");
});
refreshBootstrapButton.addEventListener("click", loadBootstrap);
installComfyButton.addEventListener("click", () =>
  startBootstrapJob(
    "/api/bootstrap/install-comfyui?backend=auto",
    installComfyButton,
    "将安装或更新 ComfyUI，并创建 venv、安装 PyTorch 和 ComfyUI 依赖。这个过程可能下载数 GB 文件，是否继续？",
  ),
);
startComfyButton.addEventListener("click", () =>
  startBootstrapJob("/api/bootstrap/start-comfyui", startComfyButton, ""),
);
modelSelector.addEventListener("change", () => {
  selectedModels[currentStep().id] = modelSelector.value;
  syncSelectedModel({ applyDefaults: true });
  renderStepIdle();
});
backButton.addEventListener("click", goBack);
continueButton.addEventListener("click", goNext);
repeatButton.addEventListener("click", submitJob);
copyDiagnosticsButton?.addEventListener("click", copyDiagnostics);
downloadDiagnosticsButton?.addEventListener("click", downloadDiagnostics);
imageInput.addEventListener("change", updateKeyframePreview);
videoInput.addEventListener("change", updateUploadedVideoMetadata);
widthInput.addEventListener("input", () => {
  if (!currentVideoResolution) updateTargetResolution();
  scheduleWorkflowPreflight();
});
heightInput.addEventListener("input", () => {
  if (!currentVideoResolution) updateTargetResolution();
  scheduleWorkflowPreflight();
});
lengthInput.addEventListener("input", scheduleWorkflowPreflight);
fpsInput.addEventListener("input", scheduleWorkflowPreflight);
stepsInput.addEventListener("input", scheduleWorkflowPreflight);
cfgInput.addEventListener("input", scheduleWorkflowPreflight);
serviceModeInputs.forEach((input) => input.addEventListener("change", previewServiceConfig));
serviceServerUrl?.addEventListener("input", previewServiceConfig);
serviceAccessToken?.addEventListener("input", previewServiceConfig);
saveServiceConfigButton?.addEventListener("click", saveServiceConfig);
reloadServiceConfigButton?.addEventListener("click", () => loadClientConfig({ quiet: false, pingRemote: true }));

async function initApp() {
  promptInput.value = defaultPrompt;
  negativeInput.value = defaultNegative;
  updateKeyframePreview();
  updateTargetResolution();
  await loadClientConfig();
  setStep("environment");
  refreshStatus();
  window.setInterval(refreshStatus, 15000);
}

initApp();
