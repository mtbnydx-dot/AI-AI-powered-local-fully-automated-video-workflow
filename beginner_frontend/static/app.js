const defaultPrompt =
  "a realistic modern esports training room, Queensland esports branding style, soft RGB lighting, students using gaming peripherals, clean commercial photography, wide angle lens, natural shadows, cinematic slow dolly-in camera movement, polished commercial video";

const defaultNegative =
  "overexposed, low quality, blurry, jpeg artifacts, distorted hands, deformed face, extra fingers, warped screens, unreadable text, watermark, subtitles, flicker, jitter, chaotic background, NSFW";

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
const missingItems = document.querySelector("#missingItems");
const smallModelRoutes = document.querySelector("#smallModelRoutes");
const bootstrapCards = document.querySelector("#bootstrapCards");
const refreshBootstrapButton = document.querySelector("#refreshBootstrapButton");
const installComfyButton = document.querySelector("#installComfyButton");
const startComfyButton = document.querySelector("#startComfyButton");
const detectEnvironmentButton = document.querySelector("#detectEnvironmentButton");
const installMissingButton = document.querySelector("#installMissingButton");
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
const jobStatus = document.querySelector("#jobStatus");
const jobMeta = document.querySelector("#jobMeta");
const resultHint = document.querySelector("#resultHint");
const resultView = document.querySelector("#resultView");
const progressBar = document.querySelector("#progressBar");
const logBox = document.querySelector("#logBox");
const keyframePreview = document.querySelector("#keyframePreview");

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
let bootstrapPollTimer = null;

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
  updateStepper();
  updateNavigation();
  updateVideoSource();
  renderStepIdle();
  if (step.id === "environment") {
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
    resultHint.textContent = "草稿完成后，点击继续进入 A14B 正片。";
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

function modelOptionsForStep(stepId) {
  return (
    environmentData?.model_options?.options?.[stepId] ||
    fallbackModelOptions[stepId] ||
    []
  );
}

function recommendedModelForStep(stepId) {
  return (
    environmentData?.model_options?.recommended?.[stepId] ||
    fallbackRecommendedModels[stepId] ||
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

function setWorkflowStatus(state, message) {
  if (!workflowStatus) return;
  workflowStatus.className = `workflow-status ${state || ""}`.trim();
  workflowStatus.textContent = message || "";
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
      setWorkflowStatus("ok", `${data.message} ${detail}`);
      return true;
    }
    const message = (data.errors || []).join("；") || data.message || "workflow 预检失败";
    setWorkflowStatus("bad", message);
    if (!quiet) appendLog(`workflow 预检失败：${message}`);
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
  const hardware = data.hardware || {};
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

  const cards = [
    {
      label: "操作系统",
      value: `${os.name || "-"} ${os.release || ""}`.trim(),
      detail: `${os.machine || "-"} | Python ${os.python || "-"}`,
      state: "ok",
    },
    {
      label: "GPU / 显存",
      value: gpuLabel,
      detail: `${vramLabel}${hardware.sum_vram_gb ? ` | 总计 ${formatGb(hardware.sum_vram_gb)}` : ""}`,
      state: hardware.accelerator === "cpu" ? "blocked" : hardware.max_vram_gb >= 80 ? "ok" : "warn",
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
      detail: ffmpeg && ffmpeg.path ? ffmpeg.path : "闪烁修复需要 ffmpeg",
      state: ffmpeg && ffmpeg.ok ? "ok" : "blocked",
    },
    {
      label: "缺失项",
      value: missingCount ? `${missingCount} 项` : "0 项",
      detail: missingCount ? "可点击一键安装补齐" : "模型和节点已就绪",
      state: missingCount ? "warn" : "ok",
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

function renderMissingItems(data) {
  const items = data.missing_installable || [];
  installMissingButton.disabled = !items.length;
  if (!items.length) {
    missingItems.innerHTML = `<p class="empty-state good">没有可一键安装的缺失项。</p>`;
    return;
  }
  missingItems.innerHTML = items
    .map(
      (item) => `
        <div class="missing-row">
          <strong>${escapeHtml(item.label || item.id)}</strong>
          <small>${escapeHtml(item.path || "")}</small>
        </div>
      `,
    )
    .join("");
}

function renderBootstrap(data) {
  bootstrapData = data;
  if (!bootstrapCards) return;
  const cards = [
    {
      label: "ComfyUI 连接",
      value: data.comfy_connected ? "已连接" : "未连接",
      detail: data.comfy_connected ? data.comfy_url : data.comfy_error || data.comfy_url,
      state: data.comfy_connected ? "ok" : "blocked",
    },
    {
      label: "ComfyUI 源码",
      value: data.comfy_repo_exists ? "已安装" : "未安装",
      detail: data.install_dir || "-",
      state: data.comfy_repo_exists ? "ok" : "warn",
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
      detail: data.git_ready ? "可安装/更新 ComfyUI" : "请先安装 Git",
      state: data.git_ready ? "ok" : "blocked",
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
  installComfyButton.disabled = !data.can_install_comfyui;
  startComfyButton.disabled = !data.can_start_comfyui && !data.comfy_connected;
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
    appendLog(`ComfyUI 安装状态检测失败：${error.message}`);
  } finally {
    refreshBootstrapButton.disabled = false;
  }
}

async function startBootstrapJob(url, button, confirmText) {
  if (confirmText && !window.confirm(confirmText)) return;
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
      installComfyButton.disabled = !(bootstrapData && bootstrapData.can_install_comfyui);
      startComfyButton.disabled = !(bootstrapData && (bootstrapData.can_start_comfyui || bootstrapData.comfy_connected));
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
  renderMissingItems(data);
  renderSmallModelRoutes(data);
}

async function loadEnvironment() {
  if (!environmentCards) return;
  detectEnvironmentButton.disabled = true;
  installMissingButton.disabled = true;
  environmentSummary.textContent = "正在检测系统、GPU、ComfyUI、ffmpeg 和模型文件。";
  try {
    const response = await fetch("/api/environment");
    const data = await response.json();
    if (!response.ok) throw new Error(formatError(data.detail || data));
    renderEnvironment(data);
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
    missingItems.innerHTML = "";
    if (smallModelRoutes) smallModelRoutes.innerHTML = "";
    setLog(`环境侦测失败：${error.message}`);
  } finally {
    detectEnvironmentButton.disabled = false;
    if (environmentData) {
      installMissingButton.disabled = !(environmentData.missing_installable || []).length;
    }
  }
}

async function startInstall() {
  const missing = environmentData?.missing_installable || [];
  if (!missing.length) {
    setLog("没有需要一键安装的缺失项。");
    return;
  }
  const confirmed = window.confirm(
    `将下载或补齐 ${missing.length} 个项目，可能包含几十 GB 模型文件。安装完成后需要重启 ComfyUI。是否继续？`,
  );
  if (!confirmed) return;

  installMissingButton.disabled = true;
  detectEnvironmentButton.disabled = true;
  setLog("正在启动安装任务。");
  try {
    const response = await fetch("/api/install", { method: "POST" });
    const job = await response.json();
    if (!response.ok) throw new Error(formatError(job.detail || job));
    pollInstall(job.id);
    installPollTimer = window.setInterval(() => pollInstall(job.id), 2500);
  } catch (error) {
    setLog(`安装任务启动失败：${error.message}`);
    installMissingButton.disabled = false;
    detectEnvironmentButton.disabled = false;
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
      appendLog(job.status === "success" ? "安装任务完成。" : "安装任务失败，请查看上方日志。");
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
  const preview =
    first.kind === "video"
      ? `<video src="${first.url}" controls playsinline></video>`
      : `<img src="${first.url}" alt="生成结果" />`;
  const links = media
    .map((item, index) => {
      const label = item.kind === "video" ? `视频 ${index + 1}` : `图片 ${index + 1}`;
      return `<a href="${item.url}" target="_blank" rel="noreferrer">${label}</a>`;
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
    if (source) fpsInput.value = 24;
  } else if (currentStep().id === "upscale") {
    source = lastRifeVideo || lastDeflickerVideo || lastFinalVideo || lastDraftVideo;
    if (source) fpsInput.value = lastRifeVideo ? currentVideoFps : 24;
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
    setStatus(true, `${data.comfyui_version} | 空闲显存 ${data.vram_free_gb} GB`);
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
    const missingTools = (data.tools || []).filter((item) => !item.ok).map((item) => item.name);
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
    completedSteps.add("keyframe");
    appendLog("关键帧已确认，进入 TI2V 试镜头。");
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
    jobStatus.textContent = "排队中";
    appendLog(`已进入队列：${data.prompt_id}`);
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
  return JSON.stringify(detail, null, 2);
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

    if (job.completed || job.status === "success") {
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
    } else if (job.status && !["queued", "running"].includes(job.status)) {
      clearInterval(pollTimer);
      pollTimer = null;
      primaryButton.disabled = false;
      repeatButton.classList.remove("hidden");
      appendLog(`任务状态：${job.status}`);
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
  const file = imageInput.files && imageInput.files[0];
  if (!file) {
    keyframePreview.innerHTML = "<p>未上传时会使用内置电竞房示例图。</p>";
    return;
  }
  const url = URL.createObjectURL(file);
  keyframePreview.innerHTML = `<img src="${url}" alt="关键帧预览" />`;
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
installMissingButton.addEventListener("click", startInstall);
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
imageInput.addEventListener("change", updateKeyframePreview);
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

promptInput.value = defaultPrompt;
negativeInput.value = defaultNegative;
updateKeyframePreview();
updateTargetResolution();
setStep("environment");
refreshStatus();
window.setInterval(refreshStatus, 15000);
