const appHeader = document.getElementById("appHeader");
const authCard = document.getElementById("authCard");
const codeAuthHint = document.getElementById("codeAuthHint");
const authCodeInput = document.getElementById("authCodeInput");
const chatBalanceLabel = document.getElementById("chatBalanceLabel");
const chatSwitch = document.getElementById("chatSwitch");
const logoutBtn = document.getElementById("logoutBtn");
const chatModal = document.getElementById("chatModal");
const chatList = document.getElementById("chatList");
const buildingsToggleBtn = document.getElementById("buildingsToggleBtn");
const playersToggleBtn = document.getElementById("playersToggleBtn");
const buildingsPanel = document.getElementById("buildingsPanel");
const buildingsList = document.getElementById("buildingsList");
const playersPanel = document.getElementById("playersPanel");
const playersSearchInput = document.getElementById("playersSearchInput");
const playersList = document.getElementById("playersList");
const playersEmpty = document.getElementById("playersEmpty");
const chatmatesList = null;
const screenLoader = document.getElementById("screenLoader");
const sceneTooltip = document.getElementById("sceneTooltip");
const sceneTooltipTitle = document.getElementById("sceneTooltipTitle");
const sceneTooltipMeta = document.getElementById("sceneTooltipMeta");
const sceneNightFilter = document.getElementById("sceneNightFilter");
const transferModal = document.getElementById("transferModal");
const transferModalScrim = document.getElementById("transferModalScrim");
const transferModalCloseBtn = document.getElementById("transferModalCloseBtn");
const transferModalTitle = document.getElementById("transferModalTitle");
const transferAmountInput = document.getElementById("transferAmountInput");
const transferSubmitBtn = document.getElementById("transferSubmitBtn");
const transferMessage = document.getElementById("transferMessage");

const sceneLayerNodes = Array.from(document.querySelectorAll("[data-scene-layer]")).reduce((acc, node) => {
    const layerName = node.dataset.sceneLayer;
    if (layerName) {
        acc[layerName] = node;
    }
    return acc;
}, {});

const SCENE_LAYER_ORDER = ["sky", "sky_elements", "background", "foreground"];
const DEFAULT_SCENE_BASE_SIZE = { width: 1920, height: 1080 };
const IDLE_ASSETS_BASE = "/static/assets/buildings";
const BUILDING_SCENE_POINTS = {
    sitopilka: { x: 895, y: 745 },
    kolodec_sita: { x: 603, y: 686 },
    sitoferma: { x: 1305, y: 469 },
    masitskaya: { x: 452, y: 340 },
    sitvolny_zavod: { x: 900, y: 397 },
};

const SCENE_ITEM_STYLE_KEYS = [
    "left",
    "right",
    "top",
    "bottom",
    "width",
    "height",
    "minWidth",
    "maxWidth",
    "minHeight",
    "maxHeight",
    "transform",
    "opacity",
    "zIndex",
    "filter",
    "mixBlendMode",
    "objectFit",
    "objectPosition",
    "pointerEvents",
];

let codeAuthInFlight = false;
let sceneAnimationFrameId = null;
let sceneBaseSize = { ...DEFAULT_SCENE_BASE_SIZE };
const sceneAnimations = [];
const sceneSpawnIntervals = [];
const sceneSpawnStartupTimeouts = [];
const sceneSpawnCleanupTimeouts = [];
const sceneSpawnedElements = [];

let buildingsPanelOpen = false;
let playersPanelOpen = false;
let buildingsPanelAutoOpened = false;
let idleBuildingsRequestInFlight = false;
let playersRequestInFlight = false;
let activeSelectedChatId = null;
let lastIdleBuildings = [];
const sceneBuildingNodes = [];
let screenLoaderDepth = 0;
let currentBalanceSits = 0;
let currentHourlyIncomeMicrosits = 0;
let currentGeyserCaughtToday = 0;
let currentGeyserDailyLimit = 10;
let sceneTooltipTimerId = null;
let sceneTooltipPointerX = 0;
let sceneTooltipPointerY = 0;
const SCENE_TOOLTIP_DELAY_MS = 1000;
let geyserSpawnConfig = null;
let geyserLoopTimeoutId = null;
let activeGeyserNode = null;
let idlePlayers = [];
let idlePlayersLoadedChatId = null;
let playersSearchValue = "";
let transferModalOpen = false;
let transferSubmitInFlight = false;
let transferRecipientPlayer = null;
let transferSenderBalance = 0;
const TRANSFER_NOTE_TEXT = "Хочется сказать, что если вы передали сит по ошибке, то это ваша проблема и решать вам её самостоятельно";
const GEYSER_CHECK_INTERVAL_MS = 20000;
const GEYSER_SPAWN_CHANCE = 0.4;
const GEYSER_REWARD_TOAST_SHOW_MS = 3000;
const GEYSER_REWARD_TOAST_FADE_MS = 2000;
const NIGHT_WINDOW_START_HOUR = 20;
const NIGHT_WINDOW_END_HOUR = 8;
const NIGHT_FILTER_EDGE_OPACITY = 0.34;
const NIGHT_FILTER_PEAK_OPACITY = 0.84;

let serverClockAnchorMs = null;
let serverClockAnchorClientMs = null;

function applySceneItemStyle(element, style) {
    if (!style || typeof style !== "object") {
        return;
    }
    SCENE_ITEM_STYLE_KEYS.forEach((styleKey) => {
        const value = style[styleKey];
        if (value === undefined || value === null) {
            return;
        }
        element.style[styleKey] = String(value);
    });
}

function normalizeHour(value, fallback) {
    const raw = Number(value);
    if (!Number.isFinite(raw)) {
        return fallback;
    }
    const hour = ((raw % 24) + 24) % 24;
    return hour;
}

function getMinutesOfDay(dateValue) {
    return (
        (dateValue.getHours() * 60)
        + dateValue.getMinutes()
        + (dateValue.getSeconds() / 60)
        + (dateValue.getMilliseconds() / 60000)
    );
}

function getWindowProgress(dateValue, startHour, endHour) {
    const startMinutes = normalizeHour(startHour, 0) * 60;
    const endMinutes = normalizeHour(endHour, 0) * 60;
    const currentMinutes = getMinutesOfDay(dateValue);

    if (startMinutes === endMinutes) {
        return 0;
    }

    if (startMinutes < endMinutes) {
        if (currentMinutes < startMinutes || currentMinutes > endMinutes) {
            return null;
        }
        return (currentMinutes - startMinutes) / (endMinutes - startMinutes);
    }

    const fullMinutes = 24 * 60;
    let elapsed = null;
    if (currentMinutes >= startMinutes) {
        elapsed = currentMinutes - startMinutes;
    } else if (currentMinutes <= endMinutes) {
        elapsed = (fullMinutes - startMinutes) + currentMinutes;
    }
    if (elapsed === null) {
        return null;
    }
    const span = (fullMinutes - startMinutes) + endMinutes;
    if (span <= 0) {
        return null;
    }
    return elapsed / span;
}

function setServerClock(serverNowIso) {
    const parsedMs = Date.parse(String(serverNowIso || ""));
    if (!Number.isFinite(parsedMs)) {
        return;
    }
    serverClockAnchorMs = parsedMs;
    serverClockAnchorClientMs = Date.now();
}

function getServerNowDate() {
    if (serverClockAnchorMs === null || serverClockAnchorClientMs === null) {
        return new Date();
    }
    const delta = Date.now() - serverClockAnchorClientMs;
    return new Date(serverClockAnchorMs + delta);
}

function getNightFilterStrength(dateValue) {
    const progress = getWindowProgress(dateValue, NIGHT_WINDOW_START_HOUR, NIGHT_WINDOW_END_HOUR);
    if (progress === null) {
        return 0;
    }

    const hour = (
        dateValue.getHours()
        + (dateValue.getMinutes() / 60)
        + (dateValue.getSeconds() / 3600)
        + (dateValue.getMilliseconds() / 3600000)
    );

    if (hour >= 20 && hour < 24) {
        const ramp = (hour - 20) / 4;
        return NIGHT_FILTER_EDGE_OPACITY + ((NIGHT_FILTER_PEAK_OPACITY - NIGHT_FILTER_EDGE_OPACITY) * ramp);
    }
    if (hour >= 0 && hour < 4) {
        return NIGHT_FILTER_PEAK_OPACITY;
    }
    if (hour >= 4 && hour < 8) {
        const downRamp = (hour - 4) / 4;
        return NIGHT_FILTER_PEAK_OPACITY - ((NIGHT_FILTER_PEAK_OPACITY - NIGHT_FILTER_EDGE_OPACITY) * downRamp);
    }

    return NIGHT_FILTER_EDGE_OPACITY;
}

function applyNightFilterForNow(dateValue = null) {
    if (!sceneNightFilter) {
        return;
    }
    const effectiveDate = dateValue instanceof Date ? dateValue : getServerNowDate();
    const strength = Math.max(0, Math.min(0.9, getNightFilterStrength(effectiveDate)));
    sceneNightFilter.style.opacity = strength.toFixed(3);
    document.body.classList.toggle("is-night", strength > 0.01);
}

function clearSceneAnimations() {
    clearGeyserLoop();
    if (sceneAnimationFrameId !== null) {
        cancelAnimationFrame(sceneAnimationFrameId);
        sceneAnimationFrameId = null;
    }
    sceneAnimations.length = 0;
    sceneSpawnIntervals.forEach((id) => clearInterval(id));
    sceneSpawnIntervals.length = 0;
    sceneSpawnStartupTimeouts.forEach((id) => clearTimeout(id));
    sceneSpawnStartupTimeouts.length = 0;
    sceneSpawnCleanupTimeouts.forEach((id) => clearTimeout(id));
    sceneSpawnCleanupTimeouts.length = 0;
    sceneSpawnedElements.forEach((element) => {
        if (element.parentNode) {
            element.parentNode.removeChild(element);
        }
    });
    sceneSpawnedElements.length = 0;
    sceneBaseSize = { ...DEFAULT_SCENE_BASE_SIZE };
    geyserSpawnConfig = null;
    applyNightFilterForNow();
    clearSceneBuildings();
}

function registerSceneAnimation(element, animationConfig) {
    if (!animationConfig || typeof animationConfig !== "object") {
        return;
    }
    const type = String(animationConfig.type || "").toLowerCase();
    if (type !== "arc-horizontal") {
        return;
    }

    const durationMs = Number(animationConfig.durationMs);
    const yMin = Number(animationConfig.yMin);
    const yMax = Number(animationConfig.yMax);
    const xPadding = Number(animationConfig.xPadding);
    const xStart = Number(animationConfig.xStart);
    const xEnd = Number(animationConfig.xEnd);
    const clockSource = String(animationConfig.clockSource || "").toLowerCase();
    const windowStartHour = Number(animationConfig.windowStartHour);
    const windowEndHour = Number(animationConfig.windowEndHour);

    const resolvedDurationMs = Number.isFinite(durationMs) && durationMs > 0 ? durationMs : 60000;
    const resolvedYMin = Number.isFinite(yMin) ? yMin : 40;
    const resolvedYMax = Number.isFinite(yMax) ? yMax : 300;
    const defaultPadding = Number.isFinite(xPadding) && xPadding >= 0 ? xPadding : 180;
    const resolvedXStart = Number.isFinite(xStart) ? xStart : -defaultPadding;
    const resolvedXEnd = Number.isFinite(xEnd) ? xEnd : defaultPadding;
    const useServerClock = clockSource === "server" && Number.isFinite(windowStartHour) && Number.isFinite(windowEndHour);

    sceneAnimations.push({
        type: "arc-horizontal",
        element,
        startedAt: performance.now(),
        durationMs: resolvedDurationMs,
        yMin: Math.min(resolvedYMin, resolvedYMax),
        yMax: Math.max(resolvedYMin, resolvedYMax),
        xStart: resolvedXStart,
        xEnd: resolvedXEnd,
        useServerClock,
        windowStartHour: useServerClock ? normalizeHour(windowStartHour, 0) : null,
        windowEndHour: useServerClock ? normalizeHour(windowEndHour, 0) : null,
    });
}

function runSceneAnimations() {
    if (!sceneAnimations.length) {
        applyNightFilterForNow();
        return;
    }

    const animate = (now) => {
        const serverNow = getServerNowDate();
        applyNightFilterForNow(serverNow);
        sceneAnimations.forEach((anim) => {
            if (anim.type !== "arc-horizontal") {
                return;
            }
            let progress = null;
            if (anim.useServerClock) {
                progress = getWindowProgress(serverNow, anim.windowStartHour, anim.windowEndHour);
            } else {
                const elapsedMs = now - anim.startedAt;
                progress = ((elapsedMs % anim.durationMs) + anim.durationMs) % anim.durationMs / anim.durationMs;
            }
            if (progress === null) {
                anim.element.style.opacity = "0";
                anim.element.style.visibility = "hidden";
                return;
            }
            anim.element.style.opacity = "1";
            anim.element.style.visibility = "visible";
            const xFrom = anim.xStart;
            const xTo = window.innerWidth + anim.xEnd;
            const x = xFrom + (xTo - xFrom) * progress;
            const y = anim.yMax - (anim.yMax - anim.yMin) * Math.sin(Math.PI * progress);
            anim.element.style.left = `${Math.round(x)}px`;
            anim.element.style.top = `${Math.round(y)}px`;
        });
        sceneAnimationFrameId = requestAnimationFrame(animate);
    };

    sceneAnimationFrameId = requestAnimationFrame(animate);
}

function removeSceneSpawnedElement(element) {
    if (!element) {
        return;
    }
    if (element.parentNode) {
        element.parentNode.removeChild(element);
    }
    const index = sceneSpawnedElements.indexOf(element);
    if (index >= 0) {
        sceneSpawnedElements.splice(index, 1);
    }
}

function resolveSceneBaseSize(sceneConfig) {
    const base = sceneConfig && typeof sceneConfig === "object" ? sceneConfig.base : null;
    const width = Number(base && typeof base === "object" ? base.width : NaN);
    const height = Number(base && typeof base === "object" ? base.height : NaN);
    return {
        width: Number.isFinite(width) && width > 0 ? width : DEFAULT_SCENE_BASE_SIZE.width,
        height: Number.isFinite(height) && height > 0 ? height : DEFAULT_SCENE_BASE_SIZE.height,
    };
}

function parseScenePoint(point) {
    if (Array.isArray(point) && point.length >= 2) {
        const x = Number(point[0]);
        const y = Number(point[1]);
        if (Number.isFinite(x) && Number.isFinite(y)) {
            return { x, y };
        }
        return null;
    }
    if (!point || typeof point !== "object") {
        return null;
    }
    const x = Number(point.x);
    const y = Number(point.y);
    if (!Number.isFinite(x) || !Number.isFinite(y)) {
        return null;
    }
    return { x, y };
}

function mapScenePointToViewport(point) {
    const parsedPoint = parseScenePoint(point);
    if (!parsedPoint) {
        return null;
    }
    const scale = Math.max(
        window.innerWidth / sceneBaseSize.width,
        window.innerHeight / sceneBaseSize.height,
    );
    return {
        x: Math.round(parsedPoint.x * scale),
        y: Math.round(parsedPoint.y * scale),
    };
}

function getSceneScale() {
    return Math.max(
        window.innerWidth / sceneBaseSize.width,
        window.innerHeight / sceneBaseSize.height,
    );
}

function hideSceneTooltip() {
    if (!sceneTooltip) {
        return;
    }
    sceneTooltip.classList.add("hidden");
}

function clearSceneTooltipTimer() {
    if (sceneTooltipTimerId !== null) {
        clearTimeout(sceneTooltipTimerId);
        sceneTooltipTimerId = null;
    }
}

function positionSceneTooltip(clientX, clientY) {
    if (!sceneTooltip || sceneTooltip.classList.contains("hidden")) {
        return;
    }
    const offset = 14;
    const margin = 8;
    const rect = sceneTooltip.getBoundingClientRect();

    let left = clientX + offset;
    let top = clientY + offset;
    if (left + rect.width > window.innerWidth - margin) {
        left = clientX - rect.width - offset;
    }
    if (top + rect.height > window.innerHeight - margin) {
        top = clientY - rect.height - offset;
    }
    left = Math.max(margin, Math.min(left, window.innerWidth - rect.width - margin));
    top = Math.max(margin, Math.min(top, window.innerHeight - rect.height - margin));
    sceneTooltip.style.left = `${Math.round(left)}px`;
    sceneTooltip.style.top = `${Math.round(top)}px`;
}

function showSceneTooltip(building, event) {
    if (!sceneTooltip || !sceneTooltipTitle || !sceneTooltipMeta) {
        return;
    }
    const level = Math.max(0, Number(building.level) || 0);
    const incomeMicrosits = Math.max(0, Math.trunc(Number(building.income_microsits_per_hour) || 0));
    sceneTooltipTitle.textContent = String(building.name || "");
    sceneTooltipMeta.textContent = `${level} ур. | ${formatMicrosits(incomeMicrosits)} миллисит в час`;
    sceneTooltip.classList.remove("hidden");
    positionSceneTooltip(event.clientX, event.clientY);
}

function scheduleSceneTooltip(building) {
    clearSceneTooltipTimer();
    sceneTooltipTimerId = window.setTimeout(() => {
        sceneTooltipTimerId = null;
        showSceneTooltip(building, { clientX: sceneTooltipPointerX, clientY: sceneTooltipPointerY });
    }, SCENE_TOOLTIP_DELAY_MS);
}

function clearSceneBuildings() {
    clearSceneTooltipTimer();
    hideSceneTooltip();
    while (sceneBuildingNodes.length) {
        const node = sceneBuildingNodes.pop();
        if (node && node.parentNode) {
            node.parentNode.removeChild(node);
        }
    }
}

function getBuildingScenePoint(building) {
    const code = String(building.building_code || "");
    if (BUILDING_SCENE_POINTS[code]) {
        return BUILDING_SCENE_POINTS[code];
    }
    const order = Math.max(1, Number(building.building_order) || 1);
    return {
        x: 760 + (order - 1) * 165,
        y: 860 - (order - 1) * 28,
    };
}

function getSceneSpriteTier(levelValue) {
    const level = Math.max(0, Math.trunc(Number(levelValue) || 0));
    if (level >= 16) {
        return 4;
    }
    if (level >= 11) {
        return 3;
    }
    if (level >= 6) {
        return 2;
    }
    if (level >= 1) {
        return 1;
    }
    return null;
}

function resolveSceneBuildingSpriteFile(baseFileName, levelValue) {
    const fileName = String(baseFileName || "");
    if (!fileName) {
        return fileName;
    }
    const tier = getSceneSpriteTier(levelValue);
    if (tier == null) {
        return fileName;
    }
    const dotIndex = fileName.lastIndexOf(".");
    if (dotIndex <= 0) {
        return `${fileName}_${tier}`;
    }
    const stem = fileName.slice(0, dotIndex);
    const ext = fileName.slice(dotIndex);
    return `${stem}_${tier}${ext}`;
}

function renderSceneBuildings(buildings) {
    clearSceneBuildings();
    const layerNode = sceneLayerNodes.foreground;
    if (!layerNode) {
        return;
    }

    const purchased = Array.isArray(buildings)
        ? buildings
            .filter((building) => (Number(building.level) || 0) > 0)
            .sort((a, b) => (Number(a.building_order) || 0) - (Number(b.building_order) || 0))
        : [];
    if (!purchased.length) {
        return;
    }

    const scale = getSceneScale();
    purchased.forEach((building) => {
        const basePoint = getBuildingScenePoint(building);
        const mappedPoint = mapScenePointToViewport(basePoint);
        if (!mappedPoint) {
            return;
        }

        const node = document.createElement("div");
        node.className = "scene-building";
        node.style.left = `${mappedPoint.x}px`;
        node.style.top = `${mappedPoint.y}px`;
        node.style.transform = `scale(${scale})`;
        node.dataset.buildingCode = String(building.building_code || "");

        const image = document.createElement("img");
        image.className = "scene-building-image";
        const baseSpriteFile = String(building.image_file || "");
        const tierSpriteFile = resolveSceneBuildingSpriteFile(baseSpriteFile, building.level);
        const baseSpritePath = buildingAssetPath(baseSpriteFile);
        const tierSpritePath = buildingAssetPath(tierSpriteFile);
        let fallbackToBaseTried = tierSpriteFile === baseSpriteFile;
        image.src = tierSpritePath;
        image.alt = String(building.name || "");
        image.decoding = "async";
        image.loading = "lazy";

        const placeholder = document.createElement("div");
        placeholder.className = "scene-building-placeholder hidden";
        placeholder.textContent = String(building.name || "");

        image.addEventListener("error", () => {
            if (!fallbackToBaseTried && baseSpriteFile) {
                fallbackToBaseTried = true;
                image.src = baseSpritePath;
                return;
            }
            image.classList.add("hidden");
            placeholder.classList.remove("hidden");
        });

        node.addEventListener("mouseenter", (event) => {
            sceneTooltipPointerX = event.clientX;
            sceneTooltipPointerY = event.clientY;
            scheduleSceneTooltip(building);
        });
        node.addEventListener("mousemove", (event) => {
            sceneTooltipPointerX = event.clientX;
            sceneTooltipPointerY = event.clientY;
            if (sceneTooltip && !sceneTooltip.classList.contains("hidden")) {
                positionSceneTooltip(event.clientX, event.clientY);
            }
        });
        node.addEventListener("mouseleave", () => {
            clearSceneTooltipTimer();
            hideSceneTooltip();
        });

        node.appendChild(image);
        node.appendChild(placeholder);
        layerNode.appendChild(node);
        sceneBuildingNodes.push(node);
    });
}

function pickRandomScenePoint(points) {
    if (!Array.isArray(points) || !points.length) {
        return null;
    }
    return points[Math.floor(Math.random() * points.length)];
}

async function fetchGeyserState() {
    const response = await fetch("/api/geyser/state", { credentials: "include" });
    if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "Ошибка состояния гейзеров");
    }
    return response.json();
}

async function catchGeyser() {
    const response = await fetch("/api/geyser/catch", {
        method: "POST",
        credentials: "include",
    });
    if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "Не удалось поймать гейзер");
    }
    return response.json();
}

function showGeyserRewardToast(rewardMillisits, clientX, clientY) {
    const toast = document.createElement("div");
    toast.className = "geyser-reward-toast";
    toast.textContent = `+${formatMicrosits(rewardMillisits)} миллисита!`;
    document.body.appendChild(toast);

    const rect = toast.getBoundingClientRect();
    const margin = 8;
    const left = Math.max(
        margin,
        Math.min(clientX - (rect.width / 2), window.innerWidth - rect.width - margin),
    );
    const top = Math.max(
        margin,
        Math.min(clientY - rect.height - 18, window.innerHeight - rect.height - margin),
    );
    toast.style.left = `${Math.round(left)}px`;
    toast.style.top = `${Math.round(top)}px`;

    window.setTimeout(() => {
        toast.classList.add("is-fading");
    }, GEYSER_REWARD_TOAST_SHOW_MS);
    window.setTimeout(() => {
        if (toast.parentNode) {
            toast.parentNode.removeChild(toast);
        }
    }, GEYSER_REWARD_TOAST_SHOW_MS + GEYSER_REWARD_TOAST_FADE_MS);
}

function removeActiveGeyserNode() {
    if (!activeGeyserNode) {
        return;
    }
    removeSceneSpawnedElement(activeGeyserNode);
    activeGeyserNode = null;
}

async function refreshGeyserStateSilently() {
    if (activeSelectedChatId == null) {
        return;
    }
    try {
        const payload = await fetchGeyserState();
        if (activeSelectedChatId == null || Number(payload.chat_id) !== Number(activeSelectedChatId)) {
            return;
        }
        setGeyserProgress(payload.caught_today, payload.daily_limit);
    } catch (_error) {
        // no-op
    }
}

function spawnCatchableGeyser(spawnConfig) {
    if (activeSelectedChatId == null || activeGeyserNode) {
        return;
    }
    const layerName = String(spawnConfig.layer || "foreground");
    const layerNode = sceneLayerNodes[layerName];
    if (!layerNode) {
        return;
    }
    const src = String(spawnConfig.src || "");
    if (!src) {
        return;
    }
    const viewportPoint = mapScenePointToViewport(pickRandomScenePoint(spawnConfig.points));
    if (!viewportPoint) {
        return;
    }

    const image = document.createElement("img");
    image.className = "scene-item scene-geyser";
    image.src = src;
    image.alt = String(spawnConfig.alt || "Geyser");
    image.decoding = "async";
    image.loading = "eager";
    image.style.left = `${viewportPoint.x}px`;
    image.style.top = `${viewportPoint.y}px`;
    if (spawnConfig.id) {
        image.dataset.sceneItemId = String(spawnConfig.id);
    }
    applySceneItemStyle(image, spawnConfig.style);
    layerNode.appendChild(image);
    sceneSpawnedElements.push(image);
    activeGeyserNode = image;

    const displayMs = Number(spawnConfig.displayMs);
    const resolvedDisplayMs = (Number.isFinite(displayMs) && displayMs > 0 ? displayMs : 2200) * 2;
    let cleanupTimeoutId = null;
    const cleanup = () => {
        if (cleanupTimeoutId !== null) {
            clearTimeout(cleanupTimeoutId);
            cleanupTimeoutId = null;
        }
        if (activeGeyserNode === image) {
            activeGeyserNode = null;
        }
        removeSceneSpawnedElement(image);
    };

    const scheduleCleanup = () => {
        cleanupTimeoutId = window.setTimeout(() => {
            cleanup();
        }, resolvedDisplayMs);
    };

    if (image.complete) {
        scheduleCleanup();
    } else {
        image.addEventListener("load", scheduleCleanup, { once: true });
    }
    image.addEventListener("error", () => {
        cleanup();
    }, { once: true });

    image.addEventListener("click", async (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (image.dataset.catching === "1") {
            return;
        }
        image.dataset.catching = "1";
        try {
            const payload = await catchGeyser();
            if (activeSelectedChatId != null && Number(payload.chat_id) === Number(activeSelectedChatId)) {
                setHeaderBalance(payload.balance);
                setGeyserProgress(payload.caught_today, payload.daily_limit);
                showGeyserRewardToast(payload.reward_millisits, event.clientX, event.clientY);
            }
        } catch (_error) {
            await refreshGeyserStateSilently();
        } finally {
            cleanup();
        }
    });
}

function clearGeyserLoop() {
    if (geyserLoopTimeoutId !== null) {
        clearTimeout(geyserLoopTimeoutId);
        geyserLoopTimeoutId = null;
    }
    removeActiveGeyserNode();
}

function startGeyserLoop() {
    clearGeyserLoop();
    if (!geyserSpawnConfig) {
        return;
    }

    const tick = async () => {
        await refreshGeyserStateSilently();
        if (
            activeSelectedChatId != null
            && currentGeyserCaughtToday < currentGeyserDailyLimit
            && Math.random() < GEYSER_SPAWN_CHANCE
        ) {
            spawnCatchableGeyser(geyserSpawnConfig);
        }
        geyserLoopTimeoutId = window.setTimeout(() => {
            void tick();
        }, GEYSER_CHECK_INTERVAL_MS);
    };

    geyserLoopTimeoutId = window.setTimeout(() => {
        void tick();
    }, GEYSER_CHECK_INTERVAL_MS);
}

function spawnTimedSceneItem(spawnConfig) {
    if (!spawnConfig || typeof spawnConfig !== "object") {
        return;
    }
    const layerName = String(spawnConfig.layer || "foreground");
    const layerNode = sceneLayerNodes[layerName];
    if (!layerNode) {
        return;
    }
    const src = String(spawnConfig.src || "");
    if (!src) {
        return;
    }
    const viewportPoint = mapScenePointToViewport(pickRandomScenePoint(spawnConfig.points));
    if (!viewportPoint) {
        return;
    }

    const image = document.createElement("img");
    image.className = "scene-item";
    image.src = src;
    image.alt = String(spawnConfig.alt || "");
    image.decoding = "async";
    image.loading = "eager";
    image.style.left = `${viewportPoint.x}px`;
    image.style.top = `${viewportPoint.y}px`;
    if (spawnConfig.id) {
        image.dataset.sceneItemId = String(spawnConfig.id);
    }
    applySceneItemStyle(image, spawnConfig.style);
    layerNode.appendChild(image);
    sceneSpawnedElements.push(image);

    const displayMs = Number(spawnConfig.displayMs);
    const resolvedDisplayMs = Number.isFinite(displayMs) && displayMs > 0 ? displayMs : 2200;
    const scheduleCleanup = () => {
        const timeoutId = window.setTimeout(() => {
            removeSceneSpawnedElement(image);
            const index = sceneSpawnCleanupTimeouts.indexOf(timeoutId);
            if (index >= 0) {
                sceneSpawnCleanupTimeouts.splice(index, 1);
            }
        }, resolvedDisplayMs);
        sceneSpawnCleanupTimeouts.push(timeoutId);
    };

    if (image.complete) {
        scheduleCleanup();
    } else {
        image.addEventListener("load", scheduleCleanup, { once: true });
    }
    image.addEventListener("error", () => removeSceneSpawnedElement(image), { once: true });
}

function registerSceneTimedSpawns(sceneConfig) {
    const events = sceneConfig && typeof sceneConfig === "object" ? sceneConfig.events : null;
    const timedSpawns = events && typeof events === "object" ? events.timedSpawns : null;
    if (!Array.isArray(timedSpawns)) {
        return;
    }

    timedSpawns.forEach((spawnConfig) => {
        if (!spawnConfig || typeof spawnConfig !== "object") {
            return;
        }
        if (String(spawnConfig.id || "") === "geyser-random") {
            geyserSpawnConfig = spawnConfig;
            return;
        }
        const intervalMs = Number(spawnConfig.intervalMs);
        const resolvedIntervalMs = Number.isFinite(intervalMs) && intervalMs > 0 ? intervalMs : 30000;
        const initialDelayMs = Number(spawnConfig.initialDelayMs);
        const resolvedInitialDelayMs = Number.isFinite(initialDelayMs) && initialDelayMs >= 0
            ? initialDelayMs
            : resolvedIntervalMs;

        const startupTimeoutId = window.setTimeout(() => {
            spawnTimedSceneItem(spawnConfig);
            const intervalId = window.setInterval(() => {
                spawnTimedSceneItem(spawnConfig);
            }, resolvedIntervalMs);
            sceneSpawnIntervals.push(intervalId);
        }, resolvedInitialDelayMs);
        sceneSpawnStartupTimeouts.push(startupTimeoutId);
    });

    startGeyserLoop();
}

function renderSceneLayer(layerName, items) {
    const layerNode = sceneLayerNodes[layerName];
    if (!layerNode) {
        return;
    }
    layerNode.innerHTML = "";
    if (!Array.isArray(items) || !items.length) {
        return;
    }

    items.forEach((item) => {
        if (!item || typeof item !== "object") {
            return;
        }
        if (String(item.kind || "image").toLowerCase() !== "image" || !item.src) {
            return;
        }
        const image = document.createElement("img");
        image.className = "scene-item";
        image.src = String(item.src);
        image.alt = String(item.alt || "");
        image.decoding = "async";
        image.loading = "lazy";
        if (item.id) {
            image.dataset.sceneItemId = String(item.id);
        }
        applySceneItemStyle(image, item.style);
        layerNode.appendChild(image);
        registerSceneAnimation(image, item.animation);
    });
}

function renderScene(sceneConfig) {
    clearSceneAnimations();
    sceneBaseSize = resolveSceneBaseSize(sceneConfig);
    const layers = sceneConfig && typeof sceneConfig === "object" ? sceneConfig.layers : null;
    SCENE_LAYER_ORDER.forEach((layerName) => {
        const layerItems = layers && typeof layers === "object" ? layers[layerName] : [];
        renderSceneLayer(layerName, layerItems);
    });
    registerSceneTimedSpawns(sceneConfig);
    runSceneAnimations();
    renderSceneBuildings(lastIdleBuildings);
}

async function loadScene() {
    try {
        const response = await fetch("/static/scene/scene.json", { credentials: "same-origin" });
        if (!response.ok) {
            throw new Error(`Scene config is unavailable (${response.status})`);
        }
        const sceneConfig = await response.json();
        renderScene(sceneConfig);
    } catch (error) {
        SCENE_LAYER_ORDER.forEach((layerName) => renderSceneLayer(layerName, []));
        console.error(error);
    }
}

function normalizeSits(value) {
    const amount = Number(value);
    if (!Number.isFinite(amount)) {
        return 0;
    }
    const rounded = Math.round(amount * 1000) / 1000;
    if (Math.abs(rounded - Math.round(rounded)) <= 1e-9) {
        return Math.round(rounded);
    }
    return rounded;
}

function sitWord(amount) {
    const normalized = normalizeSits(amount);
    if (!Number.isInteger(normalized)) {
        return "сита";
    }
    const n = Math.abs(normalized);
    if (n % 10 === 1 && n % 100 !== 11) {
        return "сит";
    }
    return "сита";
}

function formatSits(amount) {
    const normalized = normalizeSits(amount);
    if (Number.isInteger(normalized)) {
        return formatWithNarrowSpace(normalized);
    }
    const sign = normalized < 0 ? "-" : "";
    const [intPartRaw, fractionRaw] = Math.abs(normalized).toFixed(3).split(".");
    const intPart = formatWithNarrowSpace(Number(intPartRaw));
    const fraction = fractionRaw.replace(/0+$/, "");
    if (!fraction) {
        return `${sign}${intPart}`;
    }
    return `${sign}${intPart},${fraction}`;
}

function formatMicrosits(value) {
    const amount = Math.max(0, Math.trunc(Number(value) || 0));
    return formatWithNarrowSpace(amount);
}

function formatWithNarrowSpace(value) {
    const num = Number(value);
    if (!Number.isFinite(num)) {
        return "0";
    }
    const sign = num < 0 ? "-" : "";
    const digits = String(Math.abs(Math.trunc(num)));
    return `${sign}${digits.replace(/\B(?=(\d{3})+(?!\d))/g, "\u202F")}`;
}

function formatSitsFixed3(amount) {
    const value = Number(amount);
    if (!Number.isFinite(value)) {
        return "0,000";
    }
    const rounded = Math.round(value * 1000) / 1000;
    const sign = rounded < 0 ? "-" : "";
    const [intPartRaw, fractionRaw] = Math.abs(rounded).toFixed(3).split(".");
    const intPart = formatWithNarrowSpace(Number(intPartRaw));
    return `${sign}${intPart},${fractionRaw}`;
}

function renderHeaderBalance() {
    if (!chatBalanceLabel) {
        return;
    }
    const balance = normalizeSits(currentBalanceSits);
    const incomeSitsPerHour = currentHourlyIncomeMicrosits / 1000;
    chatBalanceLabel.textContent = `${currentGeyserCaughtToday}/${currentGeyserDailyLimit} гейзеров поймано | ${formatSits(balance)} (+${formatSitsFixed3(incomeSitsPerHour)}) ${sitWord(balance)}`;
}

function setHeaderBalance(amount) {
    currentBalanceSits = normalizeSits(amount);
    renderHeaderBalance();
}

function setHourlyIncomeMicrosits(amount) {
    const value = Math.max(0, Math.trunc(Number(amount) || 0));
    currentHourlyIncomeMicrosits = value;
    renderHeaderBalance();
}

function setGeyserProgress(caughtToday, dailyLimit) {
    currentGeyserCaughtToday = Math.max(0, Math.trunc(Number(caughtToday) || 0));
    const rawLimit = Math.trunc(Number(dailyLimit));
    const normalizedLimit = Number.isFinite(rawLimit) && rawLimit > 0 ? rawLimit : 10;
    currentGeyserDailyLimit = normalizedLimit;
    renderHeaderBalance();
}

function calculateHourlyIncomeMicrosits(buildings) {
    if (!Array.isArray(buildings)) {
        return 0;
    }
    return buildings.reduce((total, building) => {
        const level = Math.max(0, Number(building.level) || 0);
        if (level <= 0) {
            return total;
        }
        const income = Math.max(0, Math.trunc(Number(building.income_microsits_per_hour) || 0));
        return total + income;
    }, 0);
}

function calculateTotalBuildingLevels(buildings) {
    if (!Array.isArray(buildings)) {
        return 0;
    }
    return buildings.reduce((total, building) => total + Math.max(0, Math.trunc(Number(building.level) || 0)), 0);
}

function resolveCurrentUserDisplayName() {
    return "Сочатовец";
}

function setHidden(element, hidden) {
    if (!element) {
        return;
    }
    if (hidden) {
        element.classList.add("hidden");
    } else {
        element.classList.remove("hidden");
    }
}

function beginScreenLoading() {
    screenLoaderDepth += 1;
    if (screenLoader) {
        screenLoader.classList.remove("hidden");
    }
}

function endScreenLoading() {
    screenLoaderDepth = Math.max(0, screenLoaderDepth - 1);
    if (screenLoader && screenLoaderDepth === 0) {
        screenLoader.classList.add("hidden");
    }
}

function setLoadingMessage(message) {
    if (codeAuthHint && !authCard.classList.contains("hidden")) {
        codeAuthHint.textContent = message;
        return;
    }
    if (message) {
        console.error(message);
    }
}

function setTransferMessageNote() {
    if (!transferMessage) {
        return;
    }
    transferMessage.classList.remove("transfer-message--error");
    transferMessage.classList.add("transfer-message--note");
    transferMessage.textContent = TRANSFER_NOTE_TEXT;
}

function setTransferMessageError(message, options = {}) {
    if (!transferMessage) {
        return;
    }
    transferMessage.classList.remove("transfer-message--note");
    transferMessage.classList.add("transfer-message--error");
    transferMessage.textContent = String(message || "Ошибка передачи сита");

    if (!options.showFillBalance || !Number.isFinite(transferSenderBalance)) {
        return;
    }
    const fillBtn = document.createElement("button");
    fillBtn.type = "button";
    fillBtn.className = "transfer-message-link";
    fillBtn.textContent = "Отдать всё";
    fillBtn.addEventListener("click", () => {
        if (!transferAmountInput) {
            return;
        }
        transferAmountInput.value = String(normalizeSits(transferSenderBalance)).replace(".", ",");
        updateTransferSubmitState();
        setTransferMessageNote();
        transferAmountInput.focus();
    });
    transferMessage.appendChild(document.createTextNode(" "));
    transferMessage.appendChild(fillBtn);
}

function normalizeTransferInput(rawValue) {
    const raw = String(rawValue || "");
    let cleaned = "";
    let separatorUsed = false;
    for (const char of raw) {
        if (char >= "0" && char <= "9") {
            cleaned += char;
            continue;
        }
        if ((char === "." || char === ",") && !separatorUsed) {
            cleaned += ",";
            separatorUsed = true;
        }
    }
    return cleaned;
}

function parseTransferInputAmount(rawValue) {
    const raw = String(rawValue || "").trim().replace(/\u202f/g, "").replace(/\s+/g, "");
    if (!raw) {
        return { ok: false, code: "EMPTY", message: "Введите количество сита" };
    }
    if (!/^[+-]?\d+(?:[.,]\d+)?$/.test(raw)) {
        return { ok: false, code: "INVALID", message: "Сумма должна быть числом" };
    }
    const amount = Number(raw.replace(",", "."));
    if (!Number.isFinite(amount)) {
        return { ok: false, code: "INVALID", message: "Сумма должна быть конечным числом" };
    }
    const normalized = normalizeSits(amount);
    if (normalized < 0) {
        return { ok: false, code: "NEGATIVE", message: "Нельзя передать отрицательное значение" };
    }
    if (normalized === 0) {
        return { ok: false, code: "ZERO", message: "Введите сумму больше нуля" };
    }
    return { ok: true, amount: normalized };
}

function updateTransferSubmitState() {
    if (!transferSubmitBtn || !transferAmountInput) {
        return;
    }
    const hasInput = String(transferAmountInput.value || "").trim().length > 0;
    transferSubmitBtn.disabled = transferSubmitInFlight || !hasInput;
}

function closeTransferModal() {
    transferModalOpen = false;
    transferSubmitInFlight = false;
    transferRecipientPlayer = null;
    transferSenderBalance = 0;
    if (transferAmountInput) {
        transferAmountInput.value = "";
        transferAmountInput.placeholder = "0";
        transferAmountInput.disabled = false;
    }
    if (transferSubmitBtn) {
        transferSubmitBtn.disabled = true;
    }
    setTransferMessageNote();
    setHidden(transferModal, true);
}

async function fetchTransferBalance() {
    const response = await fetch("/api/idle/sits/balance", { credentials: "include" });
    if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "Не удалось получить текущий баланс");
    }
    return response.json();
}

async function sendSitsToPlayer(receiverUserId, amountValue) {
    const response = await fetch("/api/idle/sits/transfer", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
            receiver_user_id: Number(receiverUserId),
            amount: amountValue,
        }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        const detail = payload && payload.detail !== undefined ? payload.detail : null;
        const message = detail && typeof detail === "object"
            ? (detail.message || "Ошибка передачи сита")
            : (detail || "Ошибка передачи сита");
        const error = new Error(message);
        if (detail && typeof detail === "object") {
            error.code = detail.code || "";
            error.balance = detail.balance;
            error.requested = detail.requested;
        }
        throw error;
    }
    return payload;
}

async function openTransferModalForPlayer(player) {
    if (!transferModal || !transferAmountInput || !transferSubmitBtn || !transferModalTitle) {
        return;
    }
    transferRecipientPlayer = {
        user_id: Number(player.user_id),
        name: String(player.name || "Игрок"),
    };
    transferSubmitInFlight = false;
    transferModalOpen = true;
    transferSenderBalance = normalizeSits(currentBalanceSits);
    transferModalTitle.textContent = `Отправить сит ${transferRecipientPlayer.name}`;
    transferAmountInput.value = "";
    transferAmountInput.placeholder = formatSits(transferSenderBalance);
    transferAmountInput.disabled = true;
    transferSubmitBtn.disabled = true;
    setTransferMessageNote();
    setHidden(transferModal, false);

    try {
        const payload = await fetchTransferBalance();
        transferSenderBalance = normalizeSits(payload.balance);
        transferAmountInput.placeholder = formatSits(transferSenderBalance);
        setHeaderBalance(payload.balance);
        transferAmountInput.disabled = false;
        updateTransferSubmitState();
        transferAmountInput.focus();
    } catch (error) {
        transferAmountInput.disabled = false;
        setTransferMessageError(error.message || "Не удалось получить текущий баланс");
    }
}

async function submitTransferSits() {
    if (
        transferSubmitInFlight
        || !transferRecipientPlayer
        || !transferAmountInput
        || !transferSubmitBtn
    ) {
        return;
    }

    const parsed = parseTransferInputAmount(transferAmountInput.value);
    if (!parsed.ok) {
        setTransferMessageError(parsed.message);
        return;
    }

    if (parsed.amount + 1e-9 > transferSenderBalance) {
        setTransferMessageError(
            `Нельзя передать ${formatSits(parsed.amount)} ${sitWord(parsed.amount)}, у вас только ${formatSits(transferSenderBalance)}.`,
            { showFillBalance: true },
        );
        return;
    }

    transferSubmitInFlight = true;
    transferAmountInput.disabled = true;
    updateTransferSubmitState();

    try {
        const payload = await sendSitsToPlayer(transferRecipientPlayer.user_id, transferAmountInput.value);
        if (payload && payload.balance !== undefined) {
            transferSenderBalance = normalizeSits(payload.balance);
            setHeaderBalance(payload.balance);
        }
        closeTransferModal();
    } catch (error) {
        if (error.code === "NEGATIVE_AMOUNT") {
            setTransferMessageError("Нельзя передать отрицательное значение");
        } else if (error.code === "INSUFFICIENT_FUNDS") {
            if (error.balance !== undefined) {
                transferSenderBalance = normalizeSits(error.balance);
                setHeaderBalance(error.balance);
            }
            const requestedAmount = error.requested !== undefined ? normalizeSits(error.requested) : parsed.amount;
            setTransferMessageError(
                `Нельзя передать ${formatSits(requestedAmount)} ${sitWord(requestedAmount)}, у вас только ${formatSits(transferSenderBalance)}.`,
                { showFillBalance: true },
            );
        } else {
            setTransferMessageError(error.message || "Ошибка передачи сита");
        }
    } finally {
        transferSubmitInFlight = false;
        if (transferAmountInput) {
            transferAmountInput.disabled = false;
        }
        updateTransferSubmitState();
    }
}

function setActiveSidePanel(panelName) {
    const normalized = panelName === "buildings" || panelName === "players" ? panelName : null;
    buildingsPanelOpen = normalized === "buildings";
    playersPanelOpen = normalized === "players";

    if (buildingsToggleBtn) {
        buildingsToggleBtn.classList.toggle("is-active", buildingsPanelOpen);
        buildingsToggleBtn.setAttribute("aria-pressed", buildingsPanelOpen ? "true" : "false");
    }
    if (playersToggleBtn) {
        playersToggleBtn.classList.toggle("is-active", playersPanelOpen);
        playersToggleBtn.setAttribute("aria-pressed", playersPanelOpen ? "true" : "false");
    }
    if (buildingsPanel) {
        buildingsPanel.classList.toggle("hidden", !buildingsPanelOpen);
    }
    if (playersPanel) {
        playersPanel.classList.toggle("hidden", !playersPanelOpen);
    }
}

function setBuildingsPanelOpen(isOpen) {
    setActiveSidePanel(isOpen ? "buildings" : null);
}

function clearBuildingsPanel() {
    if (buildingsList) {
        buildingsList.innerHTML = "";
    }
}

function buildingAssetPath(fileName) {
    return `${IDLE_ASSETS_BASE}/${fileName}`;
}

function buildActionButtonConfig(building) {
    const state = String(building.state || "");
    if (state === "max_level") {
        return {
            disabled: true,
            main: "Максимальный уровень",
            sub: "",
            action: null,
        };
    }
    if (state === "zero_locked") {
        const reqLevel = Number(building.unlock_required_prev_level) || 10;
        const reqName = String(building.unlock_prev_building_name || "предыдущее здание");
        return {
            disabled: true,
            main: `Открой ${reqLevel} уровень ${reqName}`,
            sub: "",
            action: null,
        };
    }

    const costMicrosits = formatMicrosits(building.next_upgrade_cost_microsits || 0);
    const incomeDelta = formatMicrosits(building.next_income_delta_microsits || 0);
    if (state === "zero_unlocked") {
        return {
            disabled: false,
            main: `Купить за ${costMicrosits}`,
            sub: `+${incomeDelta} миллисит в час`,
            action: "buy",
        };
    }
    return {
        disabled: false,
        main: `Улучшить за ${costMicrosits}`,
        sub: `+${incomeDelta} миллисит в час`,
        action: "upgrade",
    };
}

function renderBuildingCard(building) {
    const card = document.createElement("article");
    const state = String(building.state || "default");
    card.className = `building-card building-card--${state}`;
    card.dataset.buildingCode = String(building.building_code || "");

    const iconWrap = document.createElement("div");
    iconWrap.className = "building-icon";
    const icon = document.createElement("img");
    icon.alt = String(building.name || "");
    icon.src = buildingAssetPath(building.icon_file || "");
    icon.addEventListener("error", () => {
        icon.src = buildingAssetPath(building.image_file || "");
    }, { once: true });
    iconWrap.appendChild(icon);
    card.appendChild(iconWrap);

    const main = document.createElement("div");
    main.className = "building-main";
    const titleRow = document.createElement("div");
    titleRow.className = "building-title-row";
    const title = document.createElement("h3");
    title.className = "building-title";
    title.textContent = String(building.name || "");
    titleRow.appendChild(title);
    if ((Number(building.level) || 0) > 0) {
        const level = document.createElement("span");
        level.className = "building-level";
        level.textContent = `${Number(building.level)} ур.`;
        titleRow.appendChild(level);
    }
    main.appendChild(titleRow);
    if ((Number(building.level) || 0) > 0) {
        const income = document.createElement("div");
        income.className = "building-income";
        income.textContent = `${formatMicrosits(building.income_microsits_per_hour)} миллисит в час`;
        main.appendChild(income);
    }
    card.appendChild(main);

    const lifetimeBlock = document.createElement("div");
    lifetimeBlock.className = "building-lifetime";
    if ((Number(building.level) || 0) > 0) {
        const lifetimeTitle = document.createElement("div");
        lifetimeTitle.className = "building-lifetime-title";
        lifetimeTitle.textContent = "Заработано";
        lifetimeBlock.appendChild(lifetimeTitle);
        const lifetimeValue = document.createElement("div");
        lifetimeValue.className = "building-lifetime-value";
        lifetimeValue.textContent = `${formatMicrosits(building.lifetime_earned_microsits)} миллисит`;
        lifetimeBlock.appendChild(lifetimeValue);
    }
    card.appendChild(lifetimeBlock);

    const actionWrap = document.createElement("div");
    actionWrap.className = "building-action";
    const actionConfig = buildActionButtonConfig(building);
    const actionBtn = document.createElement("button");
    actionBtn.type = "button";
    actionBtn.className = "upgrade-btn";
    actionBtn.disabled = actionConfig.disabled || !building.can_upgrade;
    actionBtn.dataset.buildingCode = String(building.building_code || "");

    const mainText = document.createElement("span");
    mainText.className = "upgrade-btn-main";
    mainText.textContent = actionConfig.main;
    actionBtn.appendChild(mainText);

    if (actionConfig.sub) {
        const subText = document.createElement("span");
        subText.className = "upgrade-btn-sub";
        subText.textContent = actionConfig.sub;
        actionBtn.appendChild(subText);
    }

    actionBtn.addEventListener("click", async () => {
        if (actionBtn.disabled) {
            return;
        }
        actionBtn.disabled = true;
        try {
            await purchaseBuilding(String(building.building_code || ""));
        } catch (error) {
            setLoadingMessage(error.message || "Ошибка покупки здания");
        } finally {
            actionBtn.disabled = false;
        }
    });

    actionWrap.appendChild(actionBtn);
    card.appendChild(actionWrap);
    return card;
}

function renderBuildingsPanel(buildings) {
    if (!buildingsList) {
        return;
    }
    buildingsList.innerHTML = "";

    const sorted = Array.isArray(buildings)
        ? [...buildings].sort((a, b) => (Number(a.building_order) || 0) - (Number(b.building_order) || 0))
        : [];

    sorted.forEach((building) => {
        buildingsList.appendChild(renderBuildingCard(building));
    });
}

function renderChatmateCard(chatmate) {
    const card = document.createElement("article");
    card.className = "chatmate-card";

    const main = document.createElement("div");
    main.className = "chatmate-main";

    const name = document.createElement("h3");
    name.className = "chatmate-name";
    name.textContent = String(chatmate.name || "Сочатовец");
    main.appendChild(name);

    const level = document.createElement("span");
    level.className = "chatmate-level";
    level.textContent = `${formatWithNarrowSpace(chatmate.totalLevels || 0)} ур.`;
    main.appendChild(level);

    card.appendChild(main);

    const actions = document.createElement("div");
    actions.className = "chatmate-actions";

    const giveBtn = document.createElement("button");
    giveBtn.type = "button";
    giveBtn.className = "chatmate-btn chatmate-btn--secondary";
    giveBtn.textContent = "Дать сит";
    giveBtn.addEventListener("click", () => {
        // placeholder for future sit transfer action
    });
    actions.appendChild(giveBtn);

    const visitBtn = document.createElement("button");
    visitBtn.type = "button";
    visitBtn.className = "chatmate-btn chatmate-btn--primary";
    visitBtn.textContent = "В гости";
    visitBtn.addEventListener("click", () => {
        // placeholder for future visit action
    });
    actions.appendChild(visitBtn);

    card.appendChild(actions);
    return card;
}

function renderChatmatesPreview(buildings) {
    if (!chatmatesList) {
        return;
    }
    chatmatesList.innerHTML = "";
    if (activeSelectedChatId == null) {
        return;
    }

    const totalLevels = calculateTotalBuildingLevels(buildings);
    const card = renderChatmateCard({
        name: resolveCurrentUserDisplayName(),
        totalLevels,
    });
    chatmatesList.appendChild(card);
}

function normalizeSearchText(value) {
    return String(value || "").trim().toLowerCase();
}

function resetPlayersData() {
    idlePlayers = [];
    idlePlayersLoadedChatId = null;
    playersSearchValue = "";
    if (playersSearchInput) {
        playersSearchInput.value = "";
    }
    renderPlayersList();
}

function renderPlayerCard(player) {
    const card = document.createElement("article");
    card.className = "player-card";

    const main = document.createElement("div");
    main.className = "player-main";

    const name = document.createElement("h3");
    name.className = "player-name";
    name.textContent = String(player.name || "Игрок");
    main.appendChild(name);

    const level = document.createElement("span");
    level.className = "player-level";
    level.textContent = `${formatWithNarrowSpace(player.total_levels || 0)} ур.`;
    main.appendChild(level);

    card.appendChild(main);

    const actions = document.createElement("div");
    actions.className = "player-actions";

    const giveBtn = document.createElement("button");
    giveBtn.type = "button";
    giveBtn.className = "player-btn player-btn--secondary";
    giveBtn.textContent = "Дать сит";
    giveBtn.addEventListener("click", async () => {
        await openTransferModalForPlayer(player);
    });
    actions.appendChild(giveBtn);

    const visitBtn = document.createElement("button");
    visitBtn.type = "button";
    visitBtn.className = "player-btn player-btn--primary";
    visitBtn.textContent = "В гости";
    visitBtn.addEventListener("click", () => {
        // placeholder for future visit action
    });
    actions.appendChild(visitBtn);

    card.appendChild(actions);
    return card;
}

function renderPlayersList() {
    if (!playersList || !playersEmpty) {
        return;
    }

    playersList.innerHTML = "";
    const query = normalizeSearchText(playersSearchValue);
    const filtered = idlePlayers.filter((player) => {
        if (!query) {
            return true;
        }
        const byName = normalizeSearchText(player.name).includes(query);
        const byNick = normalizeSearchText(player.nick).includes(query);
        return byName || byNick;
    });

    filtered.forEach((player) => {
        playersList.appendChild(renderPlayerCard(player));
    });
    playersEmpty.classList.toggle("hidden", filtered.length > 0);
}

async function fetchIdlePlayers() {
    const response = await fetch("/api/idle/players", { credentials: "include" });
    if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "Ошибка загрузки игроков");
    }
    return response.json();
}

async function ensureIdlePlayersLoaded(options = {}) {
    const force = Boolean(options.force);
    const withLoader = options.withLoader !== false;
    if (activeSelectedChatId == null) {
        return;
    }
    if (playersRequestInFlight && !force) {
        return;
    }
    if (!force && idlePlayersLoadedChatId !== null && Number(idlePlayersLoadedChatId) === Number(activeSelectedChatId)) {
        renderPlayersList();
        return;
    }

    playersRequestInFlight = true;
    if (withLoader) {
        beginScreenLoading();
    }
    try {
        const payload = await fetchIdlePlayers();
        if (activeSelectedChatId == null || Number(payload.chat_id) !== Number(activeSelectedChatId)) {
            return;
        }
        idlePlayers = Array.isArray(payload.players) ? payload.players : [];
        idlePlayersLoadedChatId = Number(payload.chat_id);
        renderPlayersList();
    } catch (error) {
        setLoadingMessage(error.message || "Ошибка загрузки игроков");
    } finally {
        playersRequestInFlight = false;
        if (withLoader) {
            endScreenLoading();
        }
    }
}

async function fetchIdleBuildings() {
    const response = await fetch("/api/idle/buildings", { credentials: "include" });
    if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "Ошибка загрузки зданий");
    }
    return response.json();
}

async function purchaseBuilding(buildingCode) {
    const response = await fetch("/api/idle/buildings/purchase", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ building_code: buildingCode }),
    });
    if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "Ошибка покупки здания");
    }
    const payload = await response.json();
    if (payload && payload.balance !== undefined) {
        setHeaderBalance(payload.balance);
    }
    if (payload && Array.isArray(payload.buildings)) {
        lastIdleBuildings = payload.buildings;
        setHourlyIncomeMicrosits(calculateHourlyIncomeMicrosits(lastIdleBuildings));
        renderBuildingsPanel(lastIdleBuildings);
        renderSceneBuildings(lastIdleBuildings);
    }
}

async function refreshIdleBuildings(options = {}) {
    const force = Boolean(options.force);
    if ((idleBuildingsRequestInFlight && !force) || activeSelectedChatId == null) {
        return;
    }
    idleBuildingsRequestInFlight = true;
    try {
        const payload = await fetchIdleBuildings();
        if (activeSelectedChatId == null || Number(payload.chat_id) !== Number(activeSelectedChatId)) {
            return;
        }
        lastIdleBuildings = payload.buildings || [];
        setHourlyIncomeMicrosits(calculateHourlyIncomeMicrosits(lastIdleBuildings));
        renderBuildingsPanel(lastIdleBuildings);
        renderSceneBuildings(lastIdleBuildings);
    } catch (error) {
        console.error(error);
    } finally {
        idleBuildingsRequestInFlight = false;
    }
}

function openChatModal(chats) {
    chatList.innerHTML = "";
    chats.forEach((chat) => {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "chat-row";
        button.textContent = chat.label;
        button.addEventListener("click", async () => {
            await selectChat(chat.chat_id);
        });
        chatList.appendChild(button);
    });
    setHidden(chatModal, false);
}

function closeChatModal() {
    setHidden(chatModal, true);
}

function fillChatSwitch(chats, selectedChatId) {
    chatSwitch.innerHTML = "";
    chats.forEach((chat) => {
        const option = document.createElement("option");
        option.value = String(chat.chat_id);
        option.textContent = chat.label;
        if (chat.chat_id === selectedChatId) {
            option.selected = true;
        }
        chatSwitch.appendChild(option);
    });
}

function renderState(state) {
    setServerClock(state && typeof state === "object" ? state.server_now_iso : null);
    applyNightFilterForNow();

    if (!state.authorized) {
        activeSelectedChatId = null;
        lastIdleBuildings = [];
        closeTransferModal();
        setHidden(appHeader, true);
        setHidden(buildingsPanel, true);
        setHidden(playersPanel, true);
        setActiveSidePanel(null);
        buildingsPanelAutoOpened = false;
        closeChatModal();
        resetPlayersData();
        clearBuildingsPanel();
        clearSceneBuildings();
        setHidden(authCard, false);
        setGeyserProgress(0, 10);
        setHourlyIncomeMicrosits(0);
        setHeaderBalance(0);
        return false;
    }

    setHidden(authCard, true);
    setHidden(appHeader, false);

    const chats = state.chats || [];
    fillChatSwitch(chats, state.selected_chat_id);

    if (!chats.length) {
        activeSelectedChatId = null;
        lastIdleBuildings = [];
        closeTransferModal();
        setActiveSidePanel(null);
        buildingsPanelAutoOpened = false;
        resetPlayersData();
        clearBuildingsPanel();
        clearSceneBuildings();
        closeChatModal();
        setGeyserProgress(0, 10);
        setHourlyIncomeMicrosits(0);
        setHeaderBalance(0);
        setLoadingMessage("Аккаунты не найдены в базе. Напишите боту в нужном чате и повторите вход.");
        return false;
    }

    if (state.selected_chat_id == null) {
        activeSelectedChatId = null;
        lastIdleBuildings = [];
        closeTransferModal();
        setActiveSidePanel(null);
        buildingsPanelAutoOpened = false;
        resetPlayersData();
        clearBuildingsPanel();
        clearSceneBuildings();
        setGeyserProgress(0, 10);
        setHourlyIncomeMicrosits(0);
        setHeaderBalance(0);
        setLoadingMessage("");
        openChatModal(chats);
        return false;
    }

    const selectedChatId = Number(state.selected_chat_id);
    if (activeSelectedChatId !== null && Number(activeSelectedChatId) !== selectedChatId) {
        setHourlyIncomeMicrosits(0);
    }
    if (activeSelectedChatId === null || Number(activeSelectedChatId) !== selectedChatId) {
        resetPlayersData();
    }

    closeChatModal();
    activeSelectedChatId = selectedChatId;
    setGeyserProgress(state.geyser_caught_today, state.geyser_daily_limit);
    setHeaderBalance(state.balance);
    if (!buildingsPanelAutoOpened) {
        setActiveSidePanel("buildings");
        buildingsPanelAutoOpened = true;
    }
    if (buildingsPanelOpen && buildingsPanel) {
        setHidden(buildingsPanel, false);
    }
    return true;
}

async function fetchState() {
    const response = await fetch("/api/state", { credentials: "include" });
    if (!response.ok) {
        throw new Error("Не удалось получить состояние");
    }
    return response.json();
}

async function selectChat(chatId, options = {}) {
    const withLoader = options.withLoader !== false;
    if (withLoader) {
        beginScreenLoading();
    }
    try {
        closeTransferModal();
        lastIdleBuildings = [];
        setHourlyIncomeMicrosits(0);
        resetPlayersData();
        clearSceneBuildings();
        clearBuildingsPanel();
        const response = await fetch("/api/select-chat", {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ chat_id: Number(chatId) }),
    });
    if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "Ошибка выбора чата");
    }
        const state = await response.json();
        const hasSelectedChat = renderState(state);
        if (hasSelectedChat) {
            await refreshIdleBuildings({ force: true });
            if (playersPanelOpen) {
                await ensureIdlePlayersLoaded({ force: true, withLoader: false });
            }
        }
    } finally {
        if (withLoader) {
            endScreenLoading();
        }
    }
}

async function refresh() {
    beginScreenLoading();
    try {
        const state = await fetchState();
        const hasSelectedChat = renderState(state);
        if (hasSelectedChat) {
            await refreshIdleBuildings({ force: true });
            if (playersPanelOpen) {
                await ensureIdlePlayersLoaded({ force: true, withLoader: false });
            }
        }
    } catch (_error) {
        setLoadingMessage("Ошибка загрузки страницы.");
    } finally {
        endScreenLoading();
    }
}

async function submitCodeAuth() {
    if (codeAuthInFlight) {
        return;
    }
    const code = (authCodeInput.value || "").replace(/\D/g, "").slice(0, 4);
    authCodeInput.value = code;

    if (code.length !== 4) {
        codeAuthHint.textContent = "Введите 4 цифры из сообщения бота.";
        return;
    }

    codeAuthInFlight = true;
    codeAuthHint.textContent = "";
    try {
        const response = await fetch("/api/auth/code", {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ code }),
        });
        if (!response.ok) {
            const payload = await response.json().catch(() => ({}));
            throw new Error(payload.detail || "Ошибка авторизации по коду.");
        }
        const state = await response.json();
        const hasSelectedChat = renderState(state);
        if (hasSelectedChat) {
            await refreshIdleBuildings({ force: true });
            if (playersPanelOpen) {
                await ensureIdlePlayersLoaded({ force: true, withLoader: false });
            }
        }
        authCodeInput.value = "";
    } catch (error) {
        codeAuthHint.textContent = error.message || "Ошибка авторизации по коду.";
    } finally {
        codeAuthInFlight = false;
    }
}

authCodeInput.addEventListener("input", async () => {
    authCodeInput.value = authCodeInput.value.replace(/\D/g, "").slice(0, 4);
    codeAuthHint.textContent = "";
    if (authCodeInput.value.length === 4) {
        await submitCodeAuth();
    }
});

authCodeInput.addEventListener("keydown", async (event) => {
    if (event.key === "Enter") {
        event.preventDefault();
        await submitCodeAuth();
    }
});

chatSwitch.addEventListener("change", async (event) => {
    try {
        await selectChat(Number(event.target.value));
    } catch (error) {
        setLoadingMessage(error.message);
    }
});

if (playersSearchInput) {
    playersSearchInput.addEventListener("input", () => {
        playersSearchValue = playersSearchInput.value || "";
        renderPlayersList();
    });
}

if (transferAmountInput) {
    transferAmountInput.addEventListener("input", () => {
        const sanitized = normalizeTransferInput(transferAmountInput.value);
        if (transferAmountInput.value !== sanitized) {
            transferAmountInput.value = sanitized;
        }
        updateTransferSubmitState();
        setTransferMessageNote();
    });

    transferAmountInput.addEventListener("paste", (event) => {
        event.preventDefault();
        const clipboardText = event.clipboardData ? event.clipboardData.getData("text") : "";
        const sanitized = normalizeTransferInput(clipboardText);
        const current = String(transferAmountInput.value || "");
        transferAmountInput.value = normalizeTransferInput(current + sanitized);
        updateTransferSubmitState();
        setTransferMessageNote();
    });

    transferAmountInput.addEventListener("keydown", async (event) => {
        if (event.key === "Enter") {
            event.preventDefault();
            await submitTransferSits();
        }
    });
}

if (transferSubmitBtn) {
    transferSubmitBtn.addEventListener("click", async () => {
        await submitTransferSits();
    });
}

if (transferModalCloseBtn) {
    transferModalCloseBtn.addEventListener("click", () => {
        closeTransferModal();
    });
}

if (transferModalScrim) {
    transferModalScrim.addEventListener("click", () => {
        closeTransferModal();
    });
}

if (transferModal) {
    transferModal.addEventListener("click", (event) => {
        if (event.target === transferModal) {
            closeTransferModal();
        }
    });
}

window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && transferModalOpen) {
        closeTransferModal();
    }
});

if (buildingsToggleBtn) {
    buildingsToggleBtn.addEventListener("click", () => {
        if (activeSelectedChatId == null) {
            return;
        }
        if (buildingsPanelOpen) {
            setActiveSidePanel(null);
            return;
        }
        setActiveSidePanel("buildings");
        if (buildingsPanelOpen) {
            void refreshIdleBuildings();
        }
    });
}

if (playersToggleBtn) {
    playersToggleBtn.addEventListener("click", () => {
        if (activeSelectedChatId == null) {
            return;
        }
        if (playersPanelOpen) {
            setActiveSidePanel(null);
            return;
        }
        setActiveSidePanel("players");
        void ensureIdlePlayersLoaded({ withLoader: true });
    });
}

logoutBtn.addEventListener("click", async () => {
    await fetch("/api/logout", { method: "POST", credentials: "include" });
    await refresh();
});

window.addEventListener("resize", () => {
    if (!lastIdleBuildings.length) {
        return;
    }
    renderSceneBuildings(lastIdleBuildings);
});

closeTransferModal();
loadScene();
refresh();
