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
const buildingsPanel = document.getElementById("buildingsPanel");
const buildingsList = document.getElementById("buildingsList");
const screenLoader = document.getElementById("screenLoader");
const sceneTooltip = document.getElementById("sceneTooltip");
const sceneTooltipTitle = document.getElementById("sceneTooltipTitle");
const sceneTooltipMeta = document.getElementById("sceneTooltipMeta");

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
let buildingsPanelAutoOpened = false;
let idleBuildingsRequestInFlight = false;
let activeSelectedChatId = null;
let lastIdleBuildings = [];
const sceneBuildingNodes = [];
let screenLoaderDepth = 0;
let currentBalanceSits = 0;
let currentHourlyIncomeMicrosits = 0;
let sceneTooltipTimerId = null;
let sceneTooltipPointerX = 0;
let sceneTooltipPointerY = 0;
const SCENE_TOOLTIP_DELAY_MS = 1000;

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

function clearSceneAnimations() {
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

    const resolvedDurationMs = Number.isFinite(durationMs) && durationMs > 0 ? durationMs : 60000;
    const resolvedYMin = Number.isFinite(yMin) ? yMin : 40;
    const resolvedYMax = Number.isFinite(yMax) ? yMax : 300;
    const defaultPadding = Number.isFinite(xPadding) && xPadding >= 0 ? xPadding : 180;
    const resolvedXStart = Number.isFinite(xStart) ? xStart : -defaultPadding;
    const resolvedXEnd = Number.isFinite(xEnd) ? xEnd : defaultPadding;

    sceneAnimations.push({
        type: "arc-horizontal",
        element,
        startedAt: performance.now(),
        durationMs: resolvedDurationMs,
        yMin: Math.min(resolvedYMin, resolvedYMax),
        yMax: Math.max(resolvedYMin, resolvedYMax),
        xStart: resolvedXStart,
        xEnd: resolvedXEnd,
    });
}

function runSceneAnimations() {
    if (!sceneAnimations.length) {
        return;
    }

    const animate = (now) => {
        sceneAnimations.forEach((anim) => {
            if (anim.type !== "arc-horizontal") {
                return;
            }
            const elapsedMs = now - anim.startedAt;
            const progress = ((elapsedMs % anim.durationMs) + anim.durationMs) % anim.durationMs / anim.durationMs;
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
        image.src = buildingAssetPath(building.image_file || "");
        image.alt = String(building.name || "");
        image.decoding = "async";
        image.loading = "lazy";

        const placeholder = document.createElement("div");
        placeholder.className = "scene-building-placeholder hidden";
        placeholder.textContent = String(building.name || "");

        image.addEventListener("error", () => {
            image.classList.add("hidden");
            placeholder.classList.remove("hidden");
        }, { once: true });

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
    chatBalanceLabel.textContent = `${formatSits(balance)} (+${formatSitsFixed3(incomeSitsPerHour)}) ${sitWord(balance)}`;
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

function setBuildingsPanelOpen(isOpen) {
    buildingsPanelOpen = Boolean(isOpen);
    if (buildingsToggleBtn) {
        buildingsToggleBtn.classList.toggle("is-active", buildingsPanelOpen);
        buildingsToggleBtn.setAttribute("aria-pressed", buildingsPanelOpen ? "true" : "false");
    }
    if (buildingsPanel) {
        buildingsPanel.classList.toggle("hidden", !buildingsPanelOpen);
    }
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
        lifetimeTitle.textContent = "Доход за всё время";
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
    if (!state.authorized) {
        activeSelectedChatId = null;
        lastIdleBuildings = [];
        setHidden(appHeader, true);
        setHidden(buildingsPanel, true);
        if (buildingsToggleBtn) {
            buildingsToggleBtn.classList.remove("is-active");
        }
        buildingsPanelOpen = false;
        buildingsPanelAutoOpened = false;
        closeChatModal();
        clearBuildingsPanel();
        clearSceneBuildings();
        setHidden(authCard, false);
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
        setBuildingsPanelOpen(false);
        buildingsPanelAutoOpened = false;
        clearBuildingsPanel();
        clearSceneBuildings();
        closeChatModal();
        setHourlyIncomeMicrosits(0);
        setHeaderBalance(0);
        setLoadingMessage("Аккаунты не найдены в базе. Напишите боту в нужном чате и повторите вход.");
        return false;
    }

    if (state.selected_chat_id == null) {
        activeSelectedChatId = null;
        lastIdleBuildings = [];
        setBuildingsPanelOpen(false);
        buildingsPanelAutoOpened = false;
        clearBuildingsPanel();
        clearSceneBuildings();
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

    closeChatModal();
    activeSelectedChatId = selectedChatId;
    setHeaderBalance(state.balance);
    if (!buildingsPanelAutoOpened) {
        setBuildingsPanelOpen(true);
        buildingsPanelAutoOpened = true;
    }
    if (buildingsPanelOpen) {
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
        lastIdleBuildings = [];
        setHourlyIncomeMicrosits(0);
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

if (buildingsToggleBtn) {
    buildingsToggleBtn.addEventListener("click", () => {
        if (activeSelectedChatId == null) {
            return;
        }
        setBuildingsPanelOpen(!buildingsPanelOpen);
        if (buildingsPanelOpen) {
            void refreshIdleBuildings();
        }
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

loadScene();
refresh();
