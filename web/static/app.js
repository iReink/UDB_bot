const appHeader = document.getElementById("appHeader");
const authCard = document.getElementById("authCard");
const codeAuthHint = document.getElementById("codeAuthHint");
const authCodeInput = document.getElementById("authCodeInput");
const chatBalanceLabel = document.getElementById("chatBalanceLabel");
const chatSwitch = document.getElementById("chatSwitch");
const chatSwitchMobile = document.getElementById("chatSwitchMobile");
const settingsWrap = document.getElementById("settingsWrap");
const settingsBtn = document.getElementById("settingsBtn");
const settingsMenu = document.getElementById("settingsMenu");
const hideBaseSwitch = document.getElementById("hideBaseSwitch");
const rejectGuestGeyserSwitch = document.getElementById("rejectGuestGeyserSwitch");
const notifyGroupSwitch = document.getElementById("notifyGroupSwitch");
const notifyGroupSoundSwitch = document.getElementById("notifyGroupSoundSwitch");
const settingsLogoutBtn = document.getElementById("settingsLogoutBtn");
const chatModal = document.getElementById("chatModal");
const chatList = document.getElementById("chatList");
const buildingsToggleBtn = document.getElementById("buildingsToggleBtn");
const playersToggleBtn = document.getElementById("playersToggleBtn");
const webChatToggleBtn = document.getElementById("webChatToggleBtn");
const chatPreviewBtn = document.getElementById("chatPreviewBtn");
const chatPreviewTrack = document.getElementById("chatPreviewTrack");
const chatPreviewCurrentAuthor = document.getElementById("chatPreviewCurrentAuthor");
const chatPreviewCurrentText = document.getElementById("chatPreviewCurrentText");
const chatPreviewNextAuthor = document.getElementById("chatPreviewNextAuthor");
const chatPreviewNextText = document.getElementById("chatPreviewNextText");
const visitHeaderTitle = document.getElementById("visitHeaderTitle");
const visitGeyserLabel = document.getElementById("visitGeyserLabel");
const visitHomeWrap = document.getElementById("visitHomeWrap");
const visitHomeBtn = document.getElementById("visitHomeBtn");
const buildingsPanel = document.getElementById("buildingsPanel");
const buildingsList = document.getElementById("buildingsList");
const playersPanel = document.getElementById("playersPanel");
const playersSearchInput = document.getElementById("playersSearchInput");
const playersList = document.getElementById("playersList");
const playersEmpty = document.getElementById("playersEmpty");
const webChatPanel = document.getElementById("webChatPanel");
const webChatMessages = document.getElementById("webChatMessages");
const webChatStatus = document.getElementById("webChatStatus");
const webChatForm = document.getElementById("webChatForm");
const webChatInput = document.getElementById("webChatInput");
const webChatSendBtn = document.getElementById("webChatSendBtn");
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
const transferAmountUnit = document.getElementById("transferAmountUnit");
const transferSubmitBtn = document.getElementById("transferSubmitBtn");
const transferMessage = document.getElementById("transferMessage");
const groupEventBanner = document.getElementById("groupEventBanner");
const groupEventBannerClose = document.getElementById("groupEventBannerClose");
const groupModal = document.getElementById("groupModal");
const groupModalScrim = document.getElementById("groupModalScrim");
const groupModalCloseBtn = document.getElementById("groupModalCloseBtn");
const groupModalTitle = document.getElementById("groupModalTitle");
const groupModalHallImage = document.getElementById("groupModalHallImage");
const groupModalAvatarLayer = document.getElementById("groupModalAvatarLayer");
const groupModalCountdownCard = document.getElementById("groupModalCountdownCard");
const groupModalCountdownTitle = document.getElementById("groupModalCountdownTitle");
const groupModalCountdownValue = document.getElementById("groupModalCountdownValue");
const groupModalResult = document.getElementById("groupModalResult");
const groupModalActions = document.getElementById("groupModalActions");
const groupModalMessage = document.getElementById("groupModalMessage");

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
const MASTUR_HALL_SCENE_POINT = { x: 1226, y: 692 };

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
let webChatPanelOpen = false;
let buildingsPanelAutoOpened = false;
let idleBuildingsRequestInFlight = false;
let playersRequestInFlight = false;
let activeSelectedChatId = null;
let activeBuildingsOwnerUserId = null;
let visitModeActive = false;
let visitTargetUserId = null;
let visitTargetName = "";
let lastIdleBuildings = [];
const sceneBuildingNodes = [];
let screenLoaderDepth = 0;
let currentBalanceSits = 0;
let currentHourlyIncomeMicrosits = 0;
let currentGeyserCaughtToday = 0;
let currentGeyserDailyLimit = 10;
let visitGeyserBlockedByOwner = false;
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
let settingsMenuOpen = false;
let settingsUpdateInFlight = false;
let webChatMessagesState = [];
let webChatLoadedChatId = null;
let webChatLoading = false;
let webChatSending = false;
let webChatPollTimeoutId = null;
let webChatOpenedForChatId = null;
let chatPreviewMessage = null;
let chatPreviewAnimationTimeoutId = null;
let webSettings = {
    hide_base: false,
    reject_geyser_catch_by_guest: false,
    notify_group_masturbation: true,
    notify_group_masturbation_sound: true,
};
const TRANSFER_NOTE_TEXT = "Хочется сказать, что если вы передали миллиситы по ошибке, то это ваша проблема и решать вам её самостоятельно";
let groupEventState = null;
let groupEventPollTimeoutId = null;
let groupEventLiveTickIntervalId = null;
let groupModalOpen = false;
let dismissedGroupEventToken = null;
let lastGroupEventPhase = "idle";
let lastGroupEventToken = null;
let groupEventKnownReminders = new Set();
let groupAvatarAnimationLoopTimeoutId = null;
let groupAvatarAnimationStopTimeoutId = null;
let groupAvatarNodes = [];
let lastGroupAvatarLayoutKey = "";
const GEYSER_CHECK_INTERVAL_MS = 20000;
const GEYSER_SPAWN_CHANCE = 0.4;
const GEYSER_REWARD_TOAST_SHOW_MS = 3000;
const GEYSER_REWARD_TOAST_FADE_MS = 2000;
const GROUP_EVENT_POLL_MS = 2500;
const GROUP_EVENT_LIVE_TICK_MS = 250;
const WEB_CHAT_POLL_MS = 2500;
const WEB_CHAT_BOTTOM_STICKY_THRESHOLD = 20;
const GROUP_HALL_ASSET = "/static/assets/masturbate/modals/sit_hall.png";
const GROUP_HALL_RESULT_ASSET = "/static/assets/masturbate/modals/sit_hall_sit.png";
const GROUP_SOUND_START_ASSET = "/static/assets/masturbate/sounds/mast_start.mp3";
const GROUP_SOUND_ACTIVE_ASSET = "/static/assets/masturbate/sounds/mast_active.mp3";
const GROUP_BUILDING_ASSET = "/static/assets/masturbate/buildings/masturhall.png";
const GROUP_BUILDING_GLOW_ASSET = "/static/assets/masturbate/buildings/masturhall_glow.png";
const GROUP_AVATARS_BASE = "/static/assets/masturbate/avatars/participants";
const GROUP_SCENE_COORD_BASE = { width: 704, height: 512 };
const GROUP_SLOT_STARTER = [{ x: 351, y: 99 }];
const GROUP_SLOT_PARTICIPANTS = [
    { x: 284, y: 167 },
    { x: 268, y: 231 },
    { x: 268, y: 395 },
    { x: 384, y: 359 },
    { x: 419, y: 167 },
    { x: 435, y: 231 },
    { x: 443, y: 295 },
    { x: 419, y: 359 },
];
const GROUP_SLOT_SPECTATORS = [
    { x: 156, y: 99 },
    { x: 156, y: 183 },
    { x: 140, y: 277 },
    { x: 132, y: 371 },
    { x: 547, y: 99 },
    { x: 547, y: 183 },
    { x: 563, y: 277 },
    { x: 571, y: 371 },
];
const GROUP_AVATAR_ANIM_CLASSES = ["is-animated", "is-animated-shake", "is-animated-spin"];
const GROUP_AVATAR_ANIM_MIN_MS = 250;
const GROUP_AVATAR_ANIM_MAX_MS = 1200;
const GROUP_AVATAR_ANIM_DURATION_MS = 5000;
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

function renderMasturHallBuilding(layerNode, scale) {
    if (!layerNode || activeSelectedChatId == null) {
        return;
    }
    const mappedPoint = mapScenePointToViewport(MASTUR_HALL_SCENE_POINT);
    if (!mappedPoint) {
        return;
    }
    const node = document.createElement("div");
    node.className = "scene-building is-masturhall";
    node.style.left = `${mappedPoint.x}px`;
    node.style.top = `${mappedPoint.y}px`;
    node.style.transform = `scale(${scale})`;
    node.dataset.buildingCode = "masturhall";

    const glow = document.createElement("img");
    glow.className = "scene-building-glow";
    glow.src = GROUP_BUILDING_GLOW_ASSET;
    glow.alt = "";
    glow.decoding = "async";
    glow.loading = "lazy";
    glow.setAttribute("aria-hidden", "true");

    const image = document.createElement("img");
    image.className = "scene-building-image";
    image.src = GROUP_BUILDING_ASSET;
    image.alt = "Мастурбашня";
    image.decoding = "async";
    image.loading = "lazy";

    node.addEventListener("mouseenter", () => {
        node.classList.add("is-hovered");
    });
    node.addEventListener("mouseleave", () => {
        node.classList.remove("is-hovered");
    });
    node.addEventListener("click", () => {
        void openGroupModal();
    });

    node.appendChild(glow);
    node.appendChild(image);
    layerNode.appendChild(node);
    sceneBuildingNodes.push(node);
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

    renderMasturHallBuilding(layerNode, scale);
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

function showGeyserRewardToast(payload, clientX, clientY) {
    const isVisitReward = Boolean(payload && payload.is_visit_reward);
    const beneficiaryName = String(payload && payload.beneficiary_name ? payload.beneficiary_name : "");
    const rewardMillisits = Math.max(0, Math.trunc(Number(payload && payload.reward_millisits) || 0));
    const visitorRewardMillisits = Math.max(0, Math.trunc(Number(payload && payload.visitor_reward_millisits) || 0));

    const toastTexts = [];
    if (isVisitReward && beneficiaryName && visitorRewardMillisits > 0) {
        toastTexts.push(`${formatMicrosits(rewardMillisits)} миллисит для ${beneficiaryName}`);
        toastTexts.push(`${formatMicrosits(visitorRewardMillisits)} миллисит для вас!`);
    } else {
        toastTexts.push(`+${formatMicrosits(rewardMillisits)} миллисита!`);
    }

    const toasts = toastTexts.map((text) => {
        const toast = document.createElement("div");
        toast.className = "geyser-reward-toast";
        toast.textContent = text;
        const driftAngle = Math.random() * Math.PI * 2;
        const driftDistance = 12 + (Math.random() * 26);
        const driftX = Math.cos(driftAngle) * driftDistance;
        const driftY = Math.sin(driftAngle) * driftDistance;
        toast.style.setProperty("--drift-x", `${driftX.toFixed(2)}px`);
        toast.style.setProperty("--drift-y", `${driftY.toFixed(2)}px`);
        toast.style.visibility = "hidden";
        document.body.appendChild(toast);
        return toast;
    });

    const margin = 8;
    const gap = 8;
    const totalWidth = toasts.reduce((sum, toast) => sum + toast.getBoundingClientRect().width, 0) + (gap * Math.max(0, toasts.length - 1));
    const maxHeight = toasts.reduce((maxValue, toast) => Math.max(maxValue, toast.getBoundingClientRect().height), 0);
    const startLeft = Math.max(
        margin,
        Math.min(clientX - (totalWidth / 2), window.innerWidth - totalWidth - margin),
    );
    const top = Math.max(
        margin,
        Math.min(clientY - maxHeight - 18, window.innerHeight - maxHeight - margin),
    );

    let cursorLeft = startLeft;
    toasts.forEach((toast) => {
        const rect = toast.getBoundingClientRect();
        toast.style.left = `${Math.round(cursorLeft)}px`;
        toast.style.top = `${Math.round(top)}px`;
        toast.style.visibility = "visible";
        cursorLeft += rect.width + gap;
    });

    window.setTimeout(() => {
        toasts.forEach((toast) => toast.classList.add("is-fading"));
    }, GEYSER_REWARD_TOAST_SHOW_MS);

    window.setTimeout(() => {
        toasts.forEach((toast) => {
            if (toast.parentNode) {
                toast.parentNode.removeChild(toast);
            }
        });
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
        setGeyserProgress(payload.caught_today, payload.daily_limit, {
            visitBlockedByOwner: Boolean(payload.visit_geyser_blocked),
        });
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
                setGeyserProgress(payload.caught_today, payload.daily_limit, {
                    visitBlockedByOwner: Boolean(payload.visit_geyser_blocked),
                });
                showGeyserRewardToast(payload, event.clientX, event.clientY);
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
            && !visitGeyserBlockedByOwner
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

function sitsToMillisits(value) {
    const sits = normalizeSits(value);
    return Math.max(0, Math.round(sits * 1000));
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
    const balanceMillisits = sitsToMillisits(currentBalanceSits);
    const incomeMillisitsPerHour = Math.max(0, Math.trunc(Number(currentHourlyIncomeMicrosits) || 0));
    chatBalanceLabel.textContent = `${currentGeyserCaughtToday}/${currentGeyserDailyLimit} гейзеров поймано | ${formatMicrosits(balanceMillisits)} (+${formatMicrosits(incomeMillisitsPerHour)}) миллисит`;
    renderVisitGeyserLabel();
}

function renderVisitGeyserLabel() {
    if (!visitGeyserLabel) {
        return;
    }
    if (!visitModeActive) {
        visitGeyserLabel.textContent = "";
        setHidden(visitGeyserLabel, true);
        return;
    }
    if (visitGeyserBlockedByOwner) {
        visitGeyserLabel.textContent = "Хозяин базы скрыл гейзеры от гостей";
        setHidden(visitGeyserLabel, false);
        return;
    }
    const targetName = String(visitTargetName || "").trim() || "сочатовца";
    visitGeyserLabel.textContent = `${currentGeyserCaughtToday}/${currentGeyserDailyLimit} гейзеров у ${targetName}`;
    setHidden(visitGeyserLabel, false);
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

function setGeyserProgress(caughtToday, dailyLimit, options = {}) {
    currentGeyserCaughtToday = Math.max(0, Math.trunc(Number(caughtToday) || 0));
    const rawLimit = Math.trunc(Number(dailyLimit));
    const normalizedLimit = Number.isFinite(rawLimit) && rawLimit > 0 ? rawLimit : 10;
    currentGeyserDailyLimit = normalizedLimit;
    if (Object.prototype.hasOwnProperty.call(options, "visitBlockedByOwner")) {
        visitGeyserBlockedByOwner = Boolean(options.visitBlockedByOwner);
    }
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

function normalizeWebSettings(settings) {
    const source = settings && typeof settings === "object" ? settings : {};
    return {
        hide_base: Boolean(source.hide_base),
        reject_geyser_catch_by_guest: Boolean(source.reject_geyser_catch_by_guest),
        notify_group_masturbation: source.notify_group_masturbation !== false,
        notify_group_masturbation_sound: source.notify_group_masturbation_sound !== false,
    };
}

function applySwitchState(button, enabled) {
    if (!button) {
        return;
    }
    button.classList.toggle("is-on", Boolean(enabled));
    button.setAttribute("aria-checked", enabled ? "true" : "false");
}

function setSettingsSwitchesDisabled(disabled) {
    if (hideBaseSwitch) {
        hideBaseSwitch.disabled = Boolean(disabled);
    }
    if (rejectGuestGeyserSwitch) {
        rejectGuestGeyserSwitch.disabled = Boolean(disabled);
    }
    if (notifyGroupSwitch) {
        notifyGroupSwitch.disabled = Boolean(disabled);
    }
    if (notifyGroupSoundSwitch) {
        notifyGroupSoundSwitch.disabled = Boolean(disabled);
    }
}

function createDefaultGroupEventState() {
    return {
        active: false,
        phase: "idle",
        event_token: null,
        server_now_ts: 0,
        prepare_until_ts: 0,
        join_until_ts: 0,
        prepare_seconds_left: 0,
        join_seconds_left: 0,
        viewer_role: "none",
        viewer_is_starter: false,
        can_start: false,
        can_remind: false,
        can_join_participant: false,
        can_join_spectator: false,
        start_cost_millisits: 1000,
        join_cost_millisits: 1000,
        participants: [],
        spectators: [],
        result: null,
        client_synced_at_ms: 0,
    };
}

function normalizeGroupMember(member, fallbackRole = "participant") {
    const source = member && typeof member === "object" ? member : {};
    return {
        user_id: Number(source.user_id) || 0,
        name: String(source.name || "Игрок"),
        sex: String(source.sex || "").toLowerCase(),
        role: String(source.role || fallbackRole),
        is_starter: Boolean(source.is_starter),
    };
}

function normalizeGroupEventState(raw) {
    const base = createDefaultGroupEventState();
    const source = raw && typeof raw === "object" ? raw : {};
    return {
        ...base,
        active: Boolean(source.active),
        phase: String(source.phase || "idle"),
        event_token: source.event_token ? String(source.event_token) : null,
        server_now_ts: Math.max(0, Number(source.server_now_ts) || 0),
        prepare_until_ts: Math.max(0, Number(source.prepare_until_ts) || 0),
        join_until_ts: Math.max(0, Number(source.join_until_ts) || 0),
        prepare_seconds_left: Math.max(0, Math.trunc(Number(source.prepare_seconds_left) || 0)),
        join_seconds_left: Math.max(0, Math.trunc(Number(source.join_seconds_left) || 0)),
        viewer_role: String(source.viewer_role || "none"),
        viewer_is_starter: Boolean(source.viewer_is_starter),
        can_start: Boolean(source.can_start),
        can_remind: Boolean(source.can_remind),
        can_join_participant: Boolean(source.can_join_participant),
        can_join_spectator: Boolean(source.can_join_spectator),
        start_cost_millisits: Math.max(0, Math.trunc(Number(source.start_cost_millisits) || 1000)),
        join_cost_millisits: Math.max(0, Math.trunc(Number(source.join_cost_millisits) || 1000)),
        participants: Array.isArray(source.participants) ? source.participants.map((item) => normalizeGroupMember(item, "participant")) : [],
        spectators: Array.isArray(source.spectators) ? source.spectators.map((item) => normalizeGroupMember(item, "spectator")) : [],
        result: source.result && typeof source.result === "object" ? source.result : null,
        client_synced_at_ms: Date.now(),
    };
}

function formatCountdownSeconds(totalSeconds) {
    const value = Math.max(0, Math.trunc(Number(totalSeconds) || 0));
    const minutes = Math.floor(value / 60);
    const seconds = value % 60;
    return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function estimateGroupServerNowTs(state) {
    const source = state && typeof state === "object" ? state : {};
    const baseNow = Math.max(0, Number(source.server_now_ts) || 0);
    if (!baseNow) {
        return Date.now() / 1000;
    }
    const syncedAtMs = Math.max(0, Number(source.client_synced_at_ms) || 0);
    if (!syncedAtMs) {
        return baseNow;
    }
    const elapsedSeconds = Math.max(0, (Date.now() - syncedAtMs) / 1000);
    return baseNow + elapsedSeconds;
}

function getLiveGroupCountdownSeconds(state, phase) {
    const source = state && typeof state === "object" ? state : {};
    const baseSeconds = phase === "preparing"
        ? Math.max(0, Number(source.prepare_seconds_left) || 0)
        : Math.max(0, Number(source.join_seconds_left) || 0);
    const syncedAtMs = Math.max(0, Number(source.client_synced_at_ms) || 0);
    if (!syncedAtMs) {
        return Math.trunc(baseSeconds);
    }
    const elapsedSeconds = Math.max(0, (Date.now() - syncedAtMs) / 1000);
    return Math.max(0, Math.ceil(baseSeconds - elapsedSeconds));
}

function isGroupTransitionToStart(state) {
    const source = state && typeof state === "object" ? state : {};
    if (!source.active) {
        return false;
    }
    if (source.phase === "preparing" && getLiveGroupCountdownSeconds(source, "preparing") <= 0) {
        return true;
    }
    if (source.phase !== "finishing") {
        return false;
    }
    const joinUntilTs = Math.max(0, Number(source.join_until_ts) || 0);
    if (!joinUntilTs) {
        return false;
    }
    return estimateGroupServerNowTs(source) < joinUntilTs;
}

function hashText(value) {
    const str = String(value || "");
    let hash = 0;
    for (let index = 0; index < str.length; index += 1) {
        hash = ((hash << 5) - hash) + str.charCodeAt(index);
        hash |= 0;
    }
    return Math.abs(hash);
}

function resolveAvatarPrefix(sexValue) {
    const sex = String(sexValue || "").toLowerCase();
    if (sex.startsWith("f") || sex.startsWith("w") || sex.startsWith("ж")) {
        return "woman";
    }
    return "man";
}

function buildAvatarFileCandidates(member, eventToken, winnerUserId = null) {
    const prefix = resolveAvatarPrefix(member.sex);
    const seed = hashText(`${eventToken}:${member.user_id}:${member.role}`);
    const index = (seed % 5) + 1;
    const suffix = String(index).padStart(2, "0");
    const isWinner = winnerUserId != null && Number(winnerUserId) === Number(member.user_id);
    const baseName = `${prefix}_${suffix}`;
    if (isWinner) {
        return [
            `${GROUP_AVATARS_BASE}/${baseName}_sit.png`,
            `${GROUP_AVATARS_BASE}/${baseName}.png`,
            `${GROUP_AVATARS_BASE}/${baseName.replace("woman", "wonam")}_sit.png`,
            `${GROUP_AVATARS_BASE}/${baseName.replace("woman", "wonam")}.png`,
        ];
    }
    return [
        `${GROUP_AVATARS_BASE}/${baseName}.png`,
        `${GROUP_AVATARS_BASE}/${baseName.replace("woman", "wonam")}.png`,
    ];
}

function pickSlotByKey(slotList, usedIndices, keySeed) {
    if (!slotList.length) {
        return null;
    }
    const baseIndex = hashText(keySeed) % slotList.length;
    for (let offset = 0; offset < slotList.length; offset += 1) {
        const index = (baseIndex + offset) % slotList.length;
        if (!usedIndices.has(index)) {
            usedIndices.add(index);
            return slotList[index];
        }
    }
    return null;
}

function randomOverflowSlot(keySeed) {
    const hash = hashText(keySeed);
    const x = 90 + (hash % 520);
    const y = 110 + (Math.floor(hash / 37) % 320);
    return { x, y };
}

function clearGroupAvatarAnimations() {
    if (groupAvatarAnimationLoopTimeoutId != null) {
        clearTimeout(groupAvatarAnimationLoopTimeoutId);
        groupAvatarAnimationLoopTimeoutId = null;
    }
    if (groupAvatarAnimationStopTimeoutId != null) {
        clearTimeout(groupAvatarAnimationStopTimeoutId);
        groupAvatarAnimationStopTimeoutId = null;
    }
    groupAvatarNodes.forEach((node) => {
        if (!node) {
            return;
        }
        GROUP_AVATAR_ANIM_CLASSES.forEach((className) => node.classList.remove(className));
    });
    groupAvatarNodes = [];
}

function scheduleNextGroupAvatarAnimation() {
    if (!groupAvatarNodes.length) {
        return;
    }
    const randomIndex = Math.floor(Math.random() * groupAvatarNodes.length);
    const avatarNode = groupAvatarNodes[randomIndex];
    if (!avatarNode) {
        return;
    }

    GROUP_AVATAR_ANIM_CLASSES.forEach((className) => avatarNode.classList.remove(className));
    const animClass = GROUP_AVATAR_ANIM_CLASSES[
        hashText(`${Date.now()}:${randomIndex}:${groupAvatarNodes.length}`) % GROUP_AVATAR_ANIM_CLASSES.length
    ];
    avatarNode.classList.add(animClass);

    groupAvatarAnimationStopTimeoutId = window.setTimeout(() => {
        avatarNode.classList.remove(animClass);
        groupAvatarAnimationStopTimeoutId = null;
        const pauseMs = GROUP_AVATAR_ANIM_MIN_MS
            + Math.floor(Math.random() * (GROUP_AVATAR_ANIM_MAX_MS - GROUP_AVATAR_ANIM_MIN_MS));
        groupAvatarAnimationLoopTimeoutId = window.setTimeout(() => {
            groupAvatarAnimationLoopTimeoutId = null;
            scheduleNextGroupAvatarAnimation();
        }, pauseMs);
    }, GROUP_AVATAR_ANIM_DURATION_MS);
}

function startGroupAvatarAnimations() {
    if (!groupAvatarNodes.length) {
        return;
    }
    const initialDelayMs = Math.floor(Math.random() * 250);
    groupAvatarAnimationLoopTimeoutId = window.setTimeout(() => {
        groupAvatarAnimationLoopTimeoutId = null;
        scheduleNextGroupAvatarAnimation();
    }, initialDelayMs);
}

function renderGroupModalAvatars(eventState) {
    if (!groupModalAvatarLayer) {
        return;
    }
    clearGroupAvatarAnimations();
    groupModalAvatarLayer.innerHTML = "";
    const renderedAvatarNodes = [];

    const participants = Array.isArray(eventState.participants) ? eventState.participants : [];
    const spectators = Array.isArray(eventState.spectators) ? eventState.spectators : [];
    const resultWinnerId = eventState.result && eventState.result.winner ? Number(eventState.result.winner.user_id) : null;
    const eventToken = String(eventState.event_token || (eventState.result && eventState.result.event_token) || "result");

    const starterMember = participants.find((member) => member.is_starter) || null;
    const participantQueue = starterMember
        ? [starterMember, ...participants.filter((member) => Number(member.user_id) !== Number(starterMember.user_id))]
        : [...participants];

    const usedParticipantSlots = new Set();
    const usedSpectatorSlots = new Set();
    const renderQueue = [];

    participantQueue.forEach((member, index) => {
        const key = `${eventToken}:participant:${member.user_id}`;
        let slot = null;
        if (index === 0 && starterMember && Number(member.user_id) === Number(starterMember.user_id)) {
            slot = GROUP_SLOT_STARTER[0];
        } else {
            slot = pickSlotByKey(GROUP_SLOT_PARTICIPANTS, usedParticipantSlots, key);
        }
        if (!slot) {
            slot = randomOverflowSlot(key);
        }
        renderQueue.push({ member, slot });
    });

    spectators.forEach((member) => {
        const key = `${eventToken}:spectator:${member.user_id}`;
        let slot = pickSlotByKey(GROUP_SLOT_SPECTATORS, usedSpectatorSlots, key);
        if (!slot) {
            slot = randomOverflowSlot(key);
        }
        renderQueue.push({ member, slot });
    });

    renderQueue.forEach(({ member, slot }) => {
        const avatar = document.createElement("div");
        avatar.className = "group-modal-avatar";
        avatar.style.left = `${(slot.x / GROUP_SCENE_COORD_BASE.width) * 100}%`;
        avatar.style.top = `${(slot.y / GROUP_SCENE_COORD_BASE.height) * 100}%`;

        const image = document.createElement("img");
        image.className = "group-modal-avatar-image";
        image.alt = String(member.name || "Участник");
        const candidates = buildAvatarFileCandidates(member, eventToken, resultWinnerId);
        let candidateIndex = 0;
        image.src = candidates[candidateIndex];
        image.addEventListener("error", () => {
            candidateIndex += 1;
            if (candidateIndex < candidates.length) {
                image.src = candidates[candidateIndex];
            }
        });
        avatar.appendChild(image);

        const name = document.createElement("div");
        name.className = "group-modal-avatar-name";
        if (resultWinnerId != null && Number(member.user_id) === Number(resultWinnerId)) {
            name.classList.add("is-winner");
        }
        name.textContent = String(member.name || "Игрок");
        avatar.appendChild(name);

        groupModalAvatarLayer.appendChild(avatar);
        renderedAvatarNodes.push(avatar);
    });
    groupAvatarNodes = renderedAvatarNodes;
    startGroupAvatarAnimations();
}

function setGroupModalMessage(message, isError = false) {
    if (!groupModalMessage) {
        return;
    }
    groupModalMessage.textContent = String(message || "");
    groupModalMessage.classList.toggle("is-error", Boolean(isError));
}

function clearGroupModalMessage() {
    setGroupModalMessage("", false);
}

function playGroupEventSound(src) {
    if (!src || !webSettings.notify_group_masturbation_sound) {
        return;
    }
    try {
        const audio = new Audio(src);
        audio.preload = "auto";
        audio.volume = 1;
        const playPromise = audio.play();
        if (playPromise && typeof playPromise.catch === "function") {
            playPromise.catch(() => {});
        }
    } catch (_error) {
        // no-op
    }
}

function playGroupStartSound() {
    playGroupEventSound(GROUP_SOUND_START_ASSET);
}

function playGroupActiveSound() {
    playGroupEventSound(GROUP_SOUND_ACTIVE_ASSET);
}

function renderGroupEventBanner() {
    if (!groupEventBanner) {
        return;
    }
    const state = groupEventState || createDefaultGroupEventState();
    const canShow = (
        activeSelectedChatId != null
        && state.active
        && state.event_token
        && state.event_token !== dismissedGroupEventToken
        && webSettings.notify_group_masturbation
    );
    setHidden(groupEventBanner, !canShow || groupModalOpen);
}

function formatGroupResultText(result) {
    if (!result || typeof result !== "object") {
        return "";
    }
    const lines = [];
    const winner = result.winner && typeof result.winner === "object" ? result.winner : null;
    const lucky = result.lucky && typeof result.lucky === "object" ? result.lucky : null;
    const luckyDick = result.lucky_dick && typeof result.lucky_dick === "object" ? result.lucky_dick : null;
    const rewardMillisits = Math.max(0, Math.trunc(Number(result.winner_reward_millisits) || 0));

    if (winner) {
        lines.push(`Победитель ${winner.name} получает ${formatMicrosits(rewardMillisits)} миллисит!`);
    }
    if (lucky) {
        lines.push(`Также немного капнуло на ${lucky.name}`);
    }
    if (luckyDick) {
        lines.push(`${luckyDick.name} так усердно мастурбировал, что член вырос на 1см`);
    }
    return lines.join("\n\n");
}

function buildGroupAvatarLayoutKey(eventState) {
    const state = eventState && typeof eventState === "object" ? eventState : {};
    const token = String(state.event_token || "no-token");
    const participants = Array.isArray(state.participants) ? state.participants : [];
    const spectators = Array.isArray(state.spectators) ? state.spectators : [];
    const winnerUserId = state.result && state.result.winner ? Number(state.result.winner.user_id) : 0;
    const membersSignature = [
        ...participants.map((member) => `${member.user_id}:p:${member.sex}:${member.is_starter ? 1 : 0}`),
        ...spectators.map((member) => `${member.user_id}:s:${member.sex}:0`),
    ].join("|");
    return `${token}::${winnerUserId}::${membersSignature}`;
}

function renderGroupModalActions(buttons) {
    if (!groupModalActions) {
        return;
    }
    groupModalActions.innerHTML = "";
    groupModalActions.classList.toggle("is-double", Array.isArray(buttons) && buttons.length === 2);
    (Array.isArray(buttons) ? buttons : []).forEach((config) => {
        const btn = document.createElement("button");
        btn.type = "button";
        btn.className = `group-modal-btn ${config.kind === "secondary" ? "group-modal-btn--secondary" : ""}`.trim();
        btn.textContent = String(config.text || "");
        btn.disabled = Boolean(config.disabled);
        if (typeof config.onClick === "function") {
            btn.addEventListener("click", config.onClick);
        }
        groupModalActions.appendChild(btn);
    });
}

async function fetchGroupEventState() {
    const response = await fetch("/api/group-event/state", { credentials: "include" });
    if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "Ошибка загрузки состояния группового ивента");
    }
    return response.json();
}

async function performGroupEventAction(path) {
    const response = await fetch(path, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: "{}",
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) {
        const detail = payload && payload.detail ? payload.detail : {};
        const message = typeof detail === "string" ? detail : (detail.message || "Ошибка действия");
        const error = new Error(message);
        if (detail && typeof detail === "object") {
            error.code = detail.code;
        }
        throw error;
    }
    return payload;
}

async function clearGroupEventResult() {
    await performGroupEventAction("/api/group-event/result/clear");
}

function renderGroupModal() {
    if (!groupModalOpen || !groupModal) {
        return;
    }
    const state = groupEventState || createDefaultGroupEventState();
    const livePrepareSeconds = getLiveGroupCountdownSeconds(state, "preparing");
    const liveJoinSeconds = getLiveGroupCountdownSeconds(state, "joining");
    const isTransitionToStart = isGroupTransitionToStart(state);

    if (groupModalHallImage) {
        const useResultHall = !state.active && state.result;
        groupModalHallImage.src = useResultHall ? GROUP_HALL_RESULT_ASSET : GROUP_HALL_ASSET;
    }

    if (groupModalTitle) {
        if (!state.active && state.result) {
            groupModalTitle.textContent = "Мастурбашня — результат";
        } else if (state.phase === "joining") {
            groupModalTitle.textContent = `Мастурбашня (${formatCountdownSeconds(liveJoinSeconds)})`;
        } else {
            groupModalTitle.textContent = "Мастурбашня";
        }
    }

    if (groupModalCountdownCard && groupModalCountdownTitle && groupModalCountdownValue) {
        const showCountdown = state.active && state.phase === "preparing";
        groupModalCountdownTitle.textContent = "До начала мастурбации:";
        groupModalCountdownValue.textContent = formatCountdownSeconds(livePrepareSeconds);
        setHidden(groupModalCountdownCard, !showCountdown);
    }

    const renderState = (!state.active && state.result)
        ? {
            ...state,
            event_token: state.result.event_token || state.event_token || "result",
            participants: Array.isArray(state.result.participants) ? state.result.participants.map((item) => normalizeGroupMember(item, "participant")) : [],
            spectators: Array.isArray(state.result.spectators) ? state.result.spectators.map((item) => normalizeGroupMember(item, "spectator")) : [],
        }
        : state;
    const nextAvatarLayoutKey = buildGroupAvatarLayoutKey(renderState);
    if (nextAvatarLayoutKey !== lastGroupAvatarLayoutKey) {
        renderGroupModalAvatars(renderState);
        lastGroupAvatarLayoutKey = nextAvatarLayoutKey;
    }

    if (groupModalResult) {
        const hasResult = !state.active && state.result;
        groupModalResult.textContent = hasResult ? formatGroupResultText(state.result) : "";
        setHidden(groupModalResult, !hasResult);
    }

    const buttons = [];
    if (!state.active && !state.result) {
        buttons.push({
            text: `Инициировать таинство групповой мастурбации (${formatMicrosits(state.start_cost_millisits)} миллисит)`,
            disabled: !state.can_start,
            onClick: async () => {
                try {
                    clearGroupModalMessage();
                    const payload = await performGroupEventAction("/api/group-event/start");
                    if (payload && payload.balance !== undefined) {
                        setHeaderBalance(payload.balance);
                    }
                    if (payload && payload.group_event) {
                        setGroupEventState(payload.group_event);
                    }
                } catch (error) {
                    setGroupModalMessage(error.message || "Недостаточно миллиситов для участия. Вы можете бесплатно посмотреть", true);
                }
            },
        });
    } else if (state.active && state.phase === "preparing") {
        if (state.viewer_role === "none") {
            const reminderAlreadyEnabled = Boolean(state.event_token && groupEventKnownReminders.has(state.event_token));
            buttons.push({
                text: "Напомнить о начале",
                disabled: reminderAlreadyEnabled || !state.can_remind,
                onClick: async () => {
                    try {
                        clearGroupModalMessage();
                        const payload = await performGroupEventAction("/api/group-event/remind");
                        if (state.event_token) {
                            groupEventKnownReminders.add(state.event_token);
                        }
                        if (payload && payload.group_event) {
                            setGroupEventState(payload.group_event);
                        }
                        setGroupModalMessage("Напоминание включено");
                    } catch (error) {
                        setGroupModalMessage(error.message || "Не удалось включить напоминание", true);
                    }
                },
            });
            if (reminderAlreadyEnabled) {
                const currentButton = buttons[buttons.length - 1];
                currentButton.text = "Напоминание включено (уведомим в ТГ)";
            }
        } else {
            buttons.push({
                text: "Вы уже участвуете в этом сеансе",
                disabled: true,
            });
        }
    } else if (state.active && state.phase === "joining") {
        if (state.viewer_role === "none") {
            buttons.push({
                text: "Посмотреть (бесплатно)",
                kind: "secondary",
                disabled: !state.can_join_spectator,
                onClick: async () => {
                    try {
                        clearGroupModalMessage();
                        const payload = await performGroupEventAction("/api/group-event/join-spectator");
                        if (payload && payload.group_event) {
                            setGroupEventState(payload.group_event);
                        }
                    } catch (error) {
                        setGroupModalMessage(error.message || "Не удалось присоединиться как зритель", true);
                    }
                },
            });
            buttons.push({
                text: `Участвовать (${formatMicrosits(state.join_cost_millisits)} мс)`,
                disabled: !state.can_join_participant,
                onClick: async () => {
                    try {
                        clearGroupModalMessage();
                        const payload = await performGroupEventAction("/api/group-event/join-participant");
                        if (payload && payload.balance !== undefined) {
                            setHeaderBalance(payload.balance);
                        }
                        if (payload && payload.group_event) {
                            setGroupEventState(payload.group_event);
                        }
                    } catch (error) {
                        setGroupModalMessage(error.message || "Недостаточно миллиситов для участия. Вы можете бесплатно посмотреть", true);
                    }
                },
            });
        } else {
            buttons.push({
                text: state.viewer_role === "spectator" ? "Вы уже в списке зрителей" : "Вы уже участвуете в этом сеансе",
                disabled: true,
            });
        }
    } else if (!state.active && state.result) {
        buttons.push({
            text: `Инициировать таинство групповой мастурбации (${formatMicrosits(state.start_cost_millisits)} миллисит)`,
            disabled: !state.can_start,
            onClick: async () => {
                try {
                    clearGroupModalMessage();
                    const payload = await performGroupEventAction("/api/group-event/start");
                    if (payload && payload.balance !== undefined) {
                        setHeaderBalance(payload.balance);
                    }
                    if (payload && payload.group_event) {
                        setGroupEventState(payload.group_event);
                    }
                } catch (error) {
                    setGroupModalMessage(error.message || "Недостаточно миллиситов для участия. Вы можете бесплатно посмотреть", true);
                }
            },
        });
    } else {
        buttons.push({
            text: isTransitionToStart ? "Уже начинаем!" : "Сеанс завершается...",
            disabled: true,
        });
    }
    renderGroupModalActions(buttons);
}

function setGroupModalOpen(isOpen) {
    groupModalOpen = Boolean(isOpen);
    setHidden(groupModal, !groupModalOpen);
    if (!groupModalOpen) {
        clearGroupEventLiveTicker();
        clearGroupAvatarAnimations();
        lastGroupAvatarLayoutKey = "";
        if (groupModalAvatarLayer) {
            groupModalAvatarLayer.innerHTML = "";
        }
        clearGroupModalMessage();
    } else {
        renderGroupModal();
        ensureGroupEventLiveTicker();
    }
    renderGroupEventBanner();
}

async function openGroupModal() {
    setGroupModalOpen(true);
    try {
        const payload = await fetchGroupEventState();
        if (payload && payload.chat_id !== undefined && Number(payload.chat_id) !== Number(activeSelectedChatId)) {
            return;
        }
        if (payload && payload.group_event) {
            setGroupEventState(payload.group_event);
        }
    } catch (error) {
        setGroupModalMessage(error.message || "Не удалось обновить состояние ивента", true);
    }
}

async function closeGroupModal(options = {}) {
    const shouldClearResult = options.clearResult !== false;
    const state = groupEventState || createDefaultGroupEventState();
    const shouldCallClear = shouldClearResult && !state.active && state.result;
    setGroupModalOpen(false);
    if (shouldCallClear) {
        try {
            const payload = await clearGroupEventResult();
            if (payload && payload.group_event) {
                setGroupEventState(payload.group_event);
            }
        } catch (_error) {
            // no-op
        }
    }
}

function setGroupEventState(nextState, options = {}) {
    const normalized = normalizeGroupEventState(nextState);
    const previousActive = Boolean(groupEventState && groupEventState.active);
    const previousPhase = lastGroupEventPhase;
    const previousToken = lastGroupEventToken;
    const nextToken = normalized.event_token;

    if (nextToken && nextToken !== previousToken) {
        if (dismissedGroupEventToken && dismissedGroupEventToken !== nextToken) {
            dismissedGroupEventToken = null;
        }
        clearGroupModalMessage();
    }

    groupEventState = normalized;
    lastGroupEventPhase = normalized.phase;
    lastGroupEventToken = nextToken;

    const becamePreparing = (
        normalized.active
        && normalized.phase === "preparing"
        && (
            !previousActive
            || previousPhase !== "preparing"
            || nextToken !== previousToken
        )
    );

    const becameJoining = (
        normalized.active
        && nextToken
        && nextToken === previousToken
        && previousPhase === "preparing"
        && normalized.phase === "joining"
    );
    if (becamePreparing) {
        playGroupStartSound();
    }
    if (becameJoining) {
        playGroupActiveSound();
    }

    if (groupModalOpen) {
        renderGroupModal();
    }
    ensureGroupEventLiveTicker();
    renderGroupEventBanner();

    if (!options.silent) {
        scheduleGroupEventPolling();
    }
}

function clearGroupEventPolling() {
    if (groupEventPollTimeoutId !== null) {
        clearTimeout(groupEventPollTimeoutId);
        groupEventPollTimeoutId = null;
    }
}

function updateHeaderHeightVar() {
    const fallback = 64;
    let nextHeight = fallback;
    if (appHeader && !appHeader.classList.contains("hidden")) {
        nextHeight = Math.max(fallback, Math.round(appHeader.offsetHeight || fallback));
    }
    document.documentElement.style.setProperty("--app-header-height", `${nextHeight}px`);
}

function clearGroupEventLiveTicker() {
    if (groupEventLiveTickIntervalId !== null) {
        clearInterval(groupEventLiveTickIntervalId);
        groupEventLiveTickIntervalId = null;
    }
}

function ensureGroupEventLiveTicker() {
    clearGroupEventLiveTicker();
    if (!groupModalOpen) {
        return;
    }
    const state = groupEventState || createDefaultGroupEventState();
    if (!state.active) {
        return;
    }
    groupEventLiveTickIntervalId = window.setInterval(() => {
        if (!groupModalOpen) {
            clearGroupEventLiveTicker();
            return;
        }
        renderGroupModal();
    }, GROUP_EVENT_LIVE_TICK_MS);
}

function scheduleGroupEventPolling() {
    clearGroupEventPolling();
    if (activeSelectedChatId == null) {
        return;
    }
    groupEventPollTimeoutId = window.setTimeout(async () => {
        try {
            const payload = await fetchGroupEventState();
            if (activeSelectedChatId == null || Number(payload.chat_id) !== Number(activeSelectedChatId)) {
                scheduleGroupEventPolling();
                return;
            }
            if (payload && payload.group_event) {
                setGroupEventState(payload.group_event, { silent: true });
            }
        } catch (_error) {
            // no-op
        } finally {
            scheduleGroupEventPolling();
        }
    }, GROUP_EVENT_POLL_MS);
}

function renderSettingsControls() {
    applySwitchState(hideBaseSwitch, webSettings.hide_base);
    applySwitchState(rejectGuestGeyserSwitch, webSettings.reject_geyser_catch_by_guest);
    applySwitchState(notifyGroupSwitch, webSettings.notify_group_masturbation);
    applySwitchState(notifyGroupSoundSwitch, webSettings.notify_group_masturbation_sound);
    setSettingsSwitchesDisabled(settingsUpdateInFlight || activeSelectedChatId == null);
}

function setWebSettings(settings) {
    webSettings = normalizeWebSettings(settings);
    renderSettingsControls();
    renderGroupEventBanner();
}

function setSettingsMenuOpen(isOpen) {
    settingsMenuOpen = Boolean(isOpen);
    if (settingsBtn) {
        settingsBtn.classList.toggle("is-active", settingsMenuOpen);
        settingsBtn.setAttribute("aria-expanded", settingsMenuOpen ? "true" : "false");
    }
    setHidden(settingsMenu, !settingsMenuOpen);
}

async function updateWebSettings(patch) {
    const response = await fetch("/api/web-settings", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(patch),
    });
    if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "Ошибка сохранения настроек");
    }
    return response.json();
}

async function toggleWebSetting(settingKey) {
    if (activeSelectedChatId == null || settingsUpdateInFlight) {
        return;
    }
    if (
        settingKey !== "hide_base"
        && settingKey !== "reject_geyser_catch_by_guest"
        && settingKey !== "notify_group_masturbation"
        && settingKey !== "notify_group_masturbation_sound"
    ) {
        return;
    }

    const previous = { ...webSettings };
    const nextValue = !Boolean(previous[settingKey]);
    setWebSettings({ ...previous, [settingKey]: nextValue });
    settingsUpdateInFlight = true;
    renderSettingsControls();
    try {
        const payload = await updateWebSettings({ [settingKey]: nextValue });
        setWebSettings(payload && payload.web_settings ? payload.web_settings : { ...previous, [settingKey]: nextValue });
    } catch (error) {
        setWebSettings(previous);
        setLoadingMessage(error.message || "Ошибка сохранения настроек");
    } finally {
        settingsUpdateInFlight = false;
        renderSettingsControls();
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

function setVisitMode(active, targetUserId = null, targetName = "") {
    visitModeActive = Boolean(active);
    visitTargetUserId = visitModeActive ? Number(targetUserId) : null;
    visitTargetName = visitModeActive ? String(targetName || "") : "";

    document.body.classList.toggle("is-visit-mode", visitModeActive);
    if (visitHeaderTitle) {
        if (visitModeActive) {
            visitHeaderTitle.textContent = `База ${visitTargetName}`;
        } else {
            visitHeaderTitle.textContent = "";
        }
        visitHeaderTitle.classList.toggle("hidden", !visitModeActive);
    }
    setHidden(visitHomeWrap, !visitModeActive);

    if (visitModeActive && buildingsPanelOpen) {
        setActiveSidePanel(null);
    }
    if (visitModeActive && buildingsPanel) {
        setHidden(buildingsPanel, true);
    }
    if (visitModeActive) {
        setSettingsMenuOpen(false);
    }
    renderVisitGeyserLabel();
}

async function startVisit(targetUserId) {
    beginScreenLoading();
    try {
        closeTransferModal();
        const response = await fetch("/api/visit/start", {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ target_user_id: Number(targetUserId) }),
        });
        if (!response.ok) {
            const payload = await response.json().catch(() => ({}));
            throw new Error(payload.detail || "Не удалось перейти в гости");
        }
        const state = await response.json();
        const hasSelectedChat = renderState(state);
        if (hasSelectedChat) {
            await refreshIdleBuildings({ force: true });
            if (playersPanelOpen) {
                await ensureIdlePlayersLoaded({ force: true, withLoader: false });
            }
            if (webChatPanelOpen) {
                await ensureWebChatLoaded({ force: true });
            }
        }
    } finally {
        endScreenLoading();
    }
}

async function leaveVisit() {
    beginScreenLoading();
    try {
        const response = await fetch("/api/visit/leave", {
            method: "POST",
            credentials: "include",
        });
        if (!response.ok) {
            const payload = await response.json().catch(() => ({}));
            throw new Error(payload.detail || "Не удалось вернуться домой");
        }
        const state = await response.json();
        const hasSelectedChat = renderState(state);
        if (hasSelectedChat) {
            await refreshIdleBuildings({ force: true });
            if (playersPanelOpen) {
                await ensureIdlePlayersLoaded({ force: true, withLoader: false });
            }
            if (webChatPanelOpen) {
                await ensureWebChatLoaded({ force: true });
            }
        }
    } finally {
        endScreenLoading();
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
    transferMessage.textContent = String(message || "Ошибка передачи миллиситов");

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
        transferAmountInput.value = String(sitsToMillisits(transferSenderBalance));
        renderTransferAmountUnit();
        updateTransferSubmitState();
        setTransferMessageNote();
        transferAmountInput.focus();
    });
    transferMessage.appendChild(document.createTextNode(" "));
    transferMessage.appendChild(fillBtn);
}

function normalizeTransferInput(rawValue) {
    return String(rawValue || "").replace(/\D+/g, "");
}

function parseTransferInputAmount(rawValue) {
    const raw = String(rawValue || "").trim().replace(/\u202f/g, "").replace(/\s+/g, "");
    if (!raw) {
        return { ok: false, code: "EMPTY", message: "Введите количество миллиситов" };
    }
    if (!/^\d+$/.test(raw)) {
        return { ok: false, code: "INVALID", message: "Можно вводить только целые миллиситы" };
    }
    const amountMillisits = Number(raw);
    if (!Number.isFinite(amountMillisits)) {
        return { ok: false, code: "INVALID", message: "Сумма должна быть конечным числом" };
    }
    if (amountMillisits <= 0) {
        return { ok: false, code: "ZERO", message: "Можно передать минимум 1 миллисит" };
    }
    return { ok: true, amountMillisits: Math.trunc(amountMillisits), amountSits: normalizeSits(amountMillisits / 1000) };
}

function renderTransferAmountUnit() {
    if (!transferAmountInput || !transferAmountUnit) {
        return;
    }
    const hasValue = String(transferAmountInput.value || "").trim().length > 0;
    transferAmountUnit.textContent = hasValue ? "миллисит" : "";
    transferAmountUnit.classList.toggle("is-visible", hasValue);
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
        transferAmountInput.placeholder = "1 сит = 1000 миллисит";
        transferAmountInput.disabled = false;
    }
    renderTransferAmountUnit();
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
            ? (detail.message || "Ошибка передачи миллиситов")
            : (detail || "Ошибка передачи миллиситов");
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
    transferModalTitle.textContent = `Отправить миллиситы ${transferRecipientPlayer.name}`;
    transferAmountInput.value = "";
    transferAmountInput.placeholder = "1 сит = 1000 миллисит";
    transferAmountInput.disabled = true;
    transferSubmitBtn.disabled = true;
    setTransferMessageNote();
    setHidden(transferModal, false);

    try {
        const payload = await fetchTransferBalance();
        transferSenderBalance = normalizeSits(payload.balance);
        transferAmountInput.placeholder = "1 сит = 1000 миллисит";
        setHeaderBalance(payload.balance);
        transferAmountInput.disabled = false;
        renderTransferAmountUnit();
        updateTransferSubmitState();
        transferAmountInput.focus();
    } catch (error) {
        transferAmountInput.disabled = false;
        renderTransferAmountUnit();
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

    if (parsed.amountSits + 1e-9 > transferSenderBalance) {
        setTransferMessageError(
            `Нельзя передать ${formatMicrosits(parsed.amountMillisits)} миллисит, у вас только ${formatMicrosits(sitsToMillisits(transferSenderBalance))}.`,
            { showFillBalance: true },
        );
        return;
    }

    transferSubmitInFlight = true;
    transferAmountInput.disabled = true;
    updateTransferSubmitState();

    try {
        const payload = await sendSitsToPlayer(transferRecipientPlayer.user_id, parsed.amountSits);
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
            const requestedAmountMillisits = error.requested !== undefined
                ? sitsToMillisits(error.requested)
                : parsed.amountMillisits;
            setTransferMessageError(
                `Нельзя передать ${formatMicrosits(requestedAmountMillisits)} миллисит, у вас только ${formatMicrosits(sitsToMillisits(transferSenderBalance))}.`,
                { showFillBalance: true },
            );
        } else {
            setTransferMessageError(error.message || "Ошибка передачи миллиситов");
        }
    } finally {
        transferSubmitInFlight = false;
        if (transferAmountInput) {
            transferAmountInput.disabled = false;
        }
        updateTransferSubmitState();
    }
}

function buildTelegramProfileUrl(rawNick) {
    const nick = String(rawNick || "").trim().replace(/^@+/, "");
    if (/^[A-Za-z0-9_]{4,64}$/.test(nick)) {
        return `https://t.me/${nick}`;
    }
    return "https://t.me/iReink";
}

function getWebChatDayParts(value) {
    if (!value) {
        return { key: "", label: "" };
    }
    const date = new Date(value);
    if (!Number.isNaN(date.getTime())) {
        const yyyy = date.getFullYear();
        const mm = String(date.getMonth() + 1).padStart(2, "0");
        const dd = String(date.getDate()).padStart(2, "0");
        return { key: `${yyyy}-${mm}-${dd}`, label: `${dd}.${mm}.${yyyy}` };
    }
    const match = String(value).match(/^(\d{4})-(\d{2})-(\d{2})/);
    if (match) {
        return { key: `${match[1]}-${match[2]}-${match[3]}`, label: `${match[3]}.${match[2]}.${match[1]}` };
    }
    return { key: "", label: "" };
}

function normalizeWebChatMessage(message) {
    const source = message && typeof message === "object" ? message : {};
    return {
        chat_id: Number(source.chat_id) || 0,
        message_id: Number(source.message_id) || 0,
        user_id: Number(source.user_id) || 0,
        author_name: String(source.author_name || source.author_nick || `Игрок ${Number(source.user_id) || 0}`),
        author_nick: String(source.author_nick || ""),
        author_link: Object.prototype.hasOwnProperty.call(source, "author_link")
            ? String(source.author_link || "")
            : buildTelegramProfileUrl(source.author_nick || ""),
        text: String(source.text || ""),
        reactions_count: Math.max(0, Math.trunc(Number(source.reactions_count) || 0)),
        date: String(source.date || ""),
        pending: Boolean(source.pending),
        failed: Boolean(source.failed),
    };
}

function formatWebChatTime(value) {
    if (!value) {
        return "";
    }
    const date = new Date(value);
    if (Number.isNaN(date.getTime())) {
        return String(value).slice(11, 16);
    }
    return date.toLocaleTimeString("ru-RU", { hour: "2-digit", minute: "2-digit" });
}

function getLastWebChatMessageId() {
    return webChatMessagesState.reduce((maxId, message) => {
        const messageId = Number(message.message_id) || 0;
        return messageId > maxId ? messageId : maxId;
    }, 0);
}

function getLatestWebChatMessage() {
    if (!webChatMessagesState.length) {
        return null;
    }
    return webChatMessagesState[webChatMessagesState.length - 1];
}

function setChatPreviewVisible(isVisible) {
    if (!chatPreviewBtn) {
        return;
    }
    setHidden(chatPreviewBtn, !isVisible);
}

function clearChatPreviewAnimation() {
    if (!chatPreviewTrack) {
        return;
    }
    chatPreviewTrack.classList.remove("is-animating");
    if (chatPreviewAnimationTimeoutId !== null) {
        clearTimeout(chatPreviewAnimationTimeoutId);
        chatPreviewAnimationTimeoutId = null;
    }
}

function applyChatPreviewCurrent(message) {
    if (!chatPreviewCurrentAuthor || !chatPreviewCurrentText) {
        return;
    }
    if (!message) {
        chatPreviewCurrentAuthor.textContent = "";
        chatPreviewCurrentText.textContent = "";
        return;
    }
    chatPreviewCurrentAuthor.textContent = String(message.author_name || "");
    chatPreviewCurrentText.textContent = String(message.text || "");
}

function applyChatPreviewNext(message) {
    if (!chatPreviewNextAuthor || !chatPreviewNextText) {
        return;
    }
    if (!message) {
        chatPreviewNextAuthor.textContent = "";
        chatPreviewNextText.textContent = "";
        return;
    }
    chatPreviewNextAuthor.textContent = String(message.author_name || "");
    chatPreviewNextText.textContent = String(message.text || "");
}

function shouldAnimateChatPreview(previousMessage, nextMessage) {
    if (!previousMessage || !nextMessage) {
        return false;
    }
    const previousId = Number(previousMessage.message_id) || 0;
    const nextId = Number(nextMessage.message_id) || 0;
    if (nextId <= previousId) {
        return false;
    }
    return true;
}

function renderChatPreview(options = {}) {
    if (!chatPreviewBtn) {
        return;
    }
    const force = Boolean(options.force);
    const latestMessage = getLatestWebChatMessage();
    const isAvailable = Boolean(activeSelectedChatId != null && latestMessage && String(latestMessage.text || "").trim());
    if (!isAvailable) {
        chatPreviewMessage = null;
        clearChatPreviewAnimation();
        applyChatPreviewCurrent(null);
        applyChatPreviewNext(null);
        setChatPreviewVisible(false);
        return;
    }

    setChatPreviewVisible(true);
    const nextMessage = {
        message_id: Number(latestMessage.message_id) || 0,
        author_name: String(latestMessage.author_name || ""),
        text: String(latestMessage.text || ""),
    };
    if (!chatPreviewMessage || force) {
        chatPreviewMessage = nextMessage;
        clearChatPreviewAnimation();
        applyChatPreviewCurrent(nextMessage);
        applyChatPreviewNext(nextMessage);
        return;
    }
    if (
        Number(chatPreviewMessage.message_id) === Number(nextMessage.message_id)
        && String(chatPreviewMessage.text) === String(nextMessage.text)
        && String(chatPreviewMessage.author_name) === String(nextMessage.author_name)
    ) {
        return;
    }

    if (!shouldAnimateChatPreview(chatPreviewMessage, nextMessage) || !chatPreviewTrack) {
        chatPreviewMessage = nextMessage;
        clearChatPreviewAnimation();
        applyChatPreviewCurrent(nextMessage);
        applyChatPreviewNext(nextMessage);
        return;
    }

    clearChatPreviewAnimation();
    applyChatPreviewCurrent(chatPreviewMessage);
    applyChatPreviewNext(nextMessage);
    chatPreviewTrack.classList.add("is-animating");
    chatPreviewAnimationTimeoutId = window.setTimeout(() => {
        chatPreviewMessage = nextMessage;
        clearChatPreviewAnimation();
        applyChatPreviewCurrent(nextMessage);
        applyChatPreviewNext(nextMessage);
    }, 390);
}

function isWebChatNearBottom(thresholdPx = WEB_CHAT_BOTTOM_STICKY_THRESHOLD) {
    if (!webChatMessages) {
        return true;
    }
    const distance = webChatMessages.scrollHeight - (webChatMessages.scrollTop + webChatMessages.clientHeight);
    return distance <= Math.max(0, Number(thresholdPx) || 0);
}

function renderWebChatMessages(options = {}) {
    if (!webChatMessages) {
        return;
    }
    const forceToBottom = Boolean(options.forceToBottom);
    const preservePosition = options.preservePosition !== false;
    const previousScrollTop = webChatMessages.scrollTop;
    let shouldStickToBottom = forceToBottom;
    if (!shouldStickToBottom && preservePosition) {
        shouldStickToBottom = isWebChatNearBottom();
    }

    webChatMessages.innerHTML = "";
    if (webChatLoading && !webChatMessagesState.length) {
        const loader = document.createElement("div");
        loader.className = "web-chat-empty";
        loader.textContent = "Загружаем сообщения...";
        webChatMessages.appendChild(loader);
        if (shouldStickToBottom) {
            webChatMessages.scrollTop = webChatMessages.scrollHeight;
        }
        return;
    }
    if (!webChatMessagesState.length) {
        const empty = document.createElement("div");
        empty.className = "web-chat-empty";
        empty.textContent = "В этом чате пока нет сообщений.";
        webChatMessages.appendChild(empty);
        if (shouldStickToBottom) {
            webChatMessages.scrollTop = webChatMessages.scrollHeight;
        }
        return;
    }

    let previousDayKey = "";
    webChatMessagesState.forEach((message) => {
        const day = getWebChatDayParts(message.date);
        if (day.key && previousDayKey && day.key !== previousDayKey) {
            const divider = document.createElement("div");
            divider.className = "web-chat-day-divider";
            divider.textContent = day.label;
            webChatMessages.appendChild(divider);
        }
        if (day.key) {
            previousDayKey = day.key;
        }

        const row = document.createElement("article");
        row.className = "web-chat-message";
        if (message.pending) {
            row.classList.add("is-pending");
        }
        if (message.failed) {
            row.classList.add("is-failed");
        }

        const head = document.createElement("div");
        head.className = "web-chat-message-head";

        const author = message.author_link ? document.createElement("a") : document.createElement("span");
        author.className = "web-chat-author";
        author.textContent = message.author_name;
        if (message.author_link) {
            author.href = message.author_link;
            author.target = "_blank";
            author.rel = "noopener noreferrer";
        }
        head.appendChild(author);

        const timeNode = document.createElement("span");
        timeNode.className = "web-chat-time";
        timeNode.textContent = message.pending ? "отправляется" : formatWebChatTime(message.date);
        head.appendChild(timeNode);

        const text = document.createElement("div");
        text.className = "web-chat-text";
        text.textContent = message.text;

        row.appendChild(head);
        row.appendChild(text);
        webChatMessages.appendChild(row);
    });
    if (shouldStickToBottom) {
        webChatMessages.scrollTop = webChatMessages.scrollHeight;
        return;
    }
    if (preservePosition) {
        const maxTop = Math.max(0, webChatMessages.scrollHeight - webChatMessages.clientHeight);
        webChatMessages.scrollTop = Math.min(previousScrollTop, maxTop);
    }
}

function setWebChatStatus(message, isError = false) {
    if (!webChatStatus) {
        return;
    }
    webChatStatus.textContent = String(message || "");
    webChatStatus.classList.toggle("is-error", Boolean(isError));
}

function mergeWebChatMessages(messages) {
    const byId = new Map();
    webChatMessagesState.forEach((message) => {
        if (!message.pending && message.message_id > 0) {
            byId.set(Number(message.message_id), message);
        }
    });
    (Array.isArray(messages) ? messages : []).forEach((message) => {
        const normalized = normalizeWebChatMessage(message);
        if (normalized.message_id > 0) {
            byId.set(Number(normalized.message_id), normalized);
        }
    });
    webChatMessagesState = Array.from(byId.values())
        .sort((a, b) => Number(a.message_id) - Number(b.message_id))
        .slice(-100);
}

async function fetchWebChatMessages(options = {}) {
    const reset = Boolean(options.reset);
    if (activeSelectedChatId == null) {
        return;
    }
    const params = new URLSearchParams();
    if (!reset) {
        const lastId = getLastWebChatMessageId();
        if (lastId > 0) {
            params.set("after_message_id", String(lastId));
        }
    }
    const url = params.toString() ? `/api/chat/messages?${params.toString()}` : "/api/chat/messages";
    const response = await fetch(url, { credentials: "include" });
    if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.detail || "Не удалось загрузить чат");
    }
    const payload = await response.json();
    if (activeSelectedChatId == null || Number(payload.chat_id) !== Number(activeSelectedChatId)) {
        return;
    }
    if (reset) {
        webChatMessagesState = [];
        webChatLoadedChatId = activeSelectedChatId;
    }
    mergeWebChatMessages(payload.messages || []);
    renderChatPreview({ force: reset });
    renderWebChatMessages({ forceToBottom: reset });
}

function clearWebChatPolling() {
    if (webChatPollTimeoutId !== null) {
        clearTimeout(webChatPollTimeoutId);
        webChatPollTimeoutId = null;
    }
}

function scheduleWebChatPolling() {
    clearWebChatPolling();
    if (activeSelectedChatId == null) {
        return;
    }
    webChatPollTimeoutId = window.setTimeout(async () => {
        try {
            await fetchWebChatMessages({ reset: false });
        } catch (error) {
            setWebChatStatus(error.message || "Не удалось обновить чат", true);
        } finally {
            scheduleWebChatPolling();
        }
    }, WEB_CHAT_POLL_MS);
}

function resetWebChatState() {
    clearWebChatPolling();
    webChatMessagesState = [];
    webChatLoadedChatId = null;
    webChatOpenedForChatId = null;
    webChatLoading = false;
    webChatSending = false;
    chatPreviewMessage = null;
    clearChatPreviewAnimation();
    renderChatPreview({ force: true });
    setWebChatStatus("");
    renderWebChatMessages();
}

async function ensureWebChatLoaded(options = {}) {
    if (!webChatPanelOpen || activeSelectedChatId == null || webChatLoading) {
        return;
    }
    const force = Boolean(options.force);
    if (!force && webChatLoadedChatId !== null && Number(webChatLoadedChatId) === Number(activeSelectedChatId)) {
        scheduleWebChatPolling();
        return;
    }
    webChatLoading = true;
    setWebChatStatus("");
    renderWebChatMessages();
    try {
        await fetchWebChatMessages({ reset: true });
    } catch (error) {
        setWebChatStatus(error.message || "Не удалось загрузить чат", true);
    } finally {
        webChatLoading = false;
        renderWebChatMessages();
        scheduleWebChatPolling();
    }
}

function updateWebChatSubmitState() {
    if (!webChatSendBtn || !webChatInput) {
        return;
    }
    webChatSendBtn.disabled = webChatSending || !(webChatInput.value || "").trim();
}

async function submitWebChatMessage() {
    if (!webChatInput || webChatSending || activeSelectedChatId == null) {
        return;
    }
    const text = (webChatInput.value || "").trim();
    if (!text) {
        updateWebChatSubmitState();
        return;
    }
    webChatSending = true;
    updateWebChatSubmitState();
    setWebChatStatus("");
    const pendingMessage = normalizeWebChatMessage({
        message_id: -Date.now(),
        author_name: "Вы",
        author_link: "",
        text,
        pending: true,
    });
    webChatMessagesState.push(pendingMessage);
    renderWebChatMessages({ forceToBottom: true });
    try {
        const response = await fetch("/api/chat/messages", {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ text }),
        });
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(payload.detail || "Не удалось отправить сообщение");
        }
        webChatInput.value = "";
        webChatMessagesState = webChatMessagesState.filter((message) => message !== pendingMessage);
        renderWebChatMessages({ forceToBottom: true });
        window.setTimeout(() => {
            void fetchWebChatMessages({ reset: false }).catch((error) => {
                setWebChatStatus(error.message || "Не удалось обновить чат", true);
            });
        }, 700);
    } catch (error) {
        pendingMessage.pending = false;
        pendingMessage.failed = true;
        setWebChatStatus(error.message || "Не удалось отправить сообщение", true);
        renderWebChatMessages({ forceToBottom: true });
    } finally {
        webChatSending = false;
        updateWebChatSubmitState();
        scheduleWebChatPolling();
    }
}

function setActiveSidePanel(panelName) {
    if (settingsMenuOpen) {
        setSettingsMenuOpen(false);
    }
    let normalized = panelName === "buildings" || panelName === "players" || panelName === "chat" ? panelName : null;
    if (visitModeActive && normalized === "buildings") {
        normalized = null;
    }
    const wasWebChatPanelOpen = webChatPanelOpen;
    buildingsPanelOpen = normalized === "buildings";
    playersPanelOpen = normalized === "players";
    webChatPanelOpen = normalized === "chat";

    if (buildingsToggleBtn) {
        buildingsToggleBtn.classList.toggle("is-active", buildingsPanelOpen);
        buildingsToggleBtn.setAttribute("aria-pressed", buildingsPanelOpen ? "true" : "false");
    }
    if (playersToggleBtn) {
        playersToggleBtn.classList.toggle("is-active", playersPanelOpen);
        playersToggleBtn.setAttribute("aria-pressed", playersPanelOpen ? "true" : "false");
    }
    if (webChatToggleBtn) {
        webChatToggleBtn.classList.toggle("is-active", webChatPanelOpen);
        webChatToggleBtn.setAttribute("aria-pressed", webChatPanelOpen ? "true" : "false");
    }
    if (buildingsPanel) {
        buildingsPanel.classList.toggle("hidden", !buildingsPanelOpen);
    }
    if (playersPanel) {
        playersPanel.classList.toggle("hidden", !playersPanelOpen);
    }
    if (webChatPanel) {
        webChatPanel.classList.toggle("hidden", !webChatPanelOpen);
    }
    if (webChatPanelOpen) {
        if (webChatInput) {
            window.setTimeout(() => {
                webChatInput.focus({ preventScroll: true });
            }, 0);
        }
        const isFirstOpenForCurrentChat = (
            !wasWebChatPanelOpen
            && activeSelectedChatId != null
            && Number(webChatOpenedForChatId) !== Number(activeSelectedChatId)
        );
        if (isFirstOpenForCurrentChat) {
            webChatOpenedForChatId = Number(activeSelectedChatId);
            window.setTimeout(() => {
                renderWebChatMessages({ forceToBottom: true, preservePosition: false });
            }, 0);
        }
        void ensureWebChatLoaded();
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
    const isCurrentVisitTarget = visitModeActive && Number(visitTargetUserId) === Number(player.user_id);
    if (isCurrentVisitTarget) {
        visitBtn.textContent = "В гостях";
        visitBtn.disabled = true;
    }
    visitBtn.addEventListener("click", async () => {
        if (visitBtn.disabled) {
            return;
        }
        try {
            await startVisit(player.user_id);
        } catch (error) {
            setLoadingMessage(error.message || "Не удалось перейти в гости");
        }
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
        activeBuildingsOwnerUserId = payload.buildings_owner_user_id !== undefined
            ? Number(payload.buildings_owner_user_id)
            : Number(payload.user_id);
        if (payload && payload.visit && payload.visit.active) {
            setVisitMode(true, Number(payload.visit.user_id), String(payload.visit.name || ""));
        } else {
            setVisitMode(false);
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
    if (chatSwitch) {
        chatSwitch.innerHTML = "";
    }
    if (chatSwitchMobile) {
        chatSwitchMobile.innerHTML = "";
    }
    chats.forEach((chat) => {
        const value = String(chat.chat_id);

        if (chatSwitch) {
            const option = document.createElement("option");
            option.value = value;
            option.textContent = chat.label;
            if (chat.chat_id === selectedChatId) {
                option.selected = true;
            }
            chatSwitch.appendChild(option);
        }

        if (chatSwitchMobile) {
            const optionMobile = document.createElement("option");
            optionMobile.value = value;
            optionMobile.textContent = chat.label;
            if (chat.chat_id === selectedChatId) {
                optionMobile.selected = true;
            }
            chatSwitchMobile.appendChild(optionMobile);
        }
    });
}

function renderState(state) {
    setServerClock(state && typeof state === "object" ? state.server_now_iso : null);
    applyNightFilterForNow();
    updateHeaderHeightVar();

    if (!state.authorized) {
        activeSelectedChatId = null;
        groupEventKnownReminders = new Set();
        activeBuildingsOwnerUserId = null;
        lastIdleBuildings = [];
        setVisitMode(false);
        closeTransferModal();
        setHidden(appHeader, true);
        updateHeaderHeightVar();
        setHidden(buildingsPanel, true);
        setHidden(playersPanel, true);
        setHidden(webChatPanel, true);
        setActiveSidePanel(null);
        buildingsPanelAutoOpened = false;
        closeChatModal();
        resetPlayersData();
        resetWebChatState();
        clearBuildingsPanel();
        clearSceneBuildings();
        setHidden(authCard, false);
        setGeyserProgress(0, 10, { visitBlockedByOwner: false });
        setWebSettings({
            hide_base: false,
            reject_geyser_catch_by_guest: false,
            notify_group_masturbation: true,
            notify_group_masturbation_sound: true,
        });
        setSettingsMenuOpen(false);
        clearGroupEventPolling();
        setGroupEventState(createDefaultGroupEventState(), { silent: true });
        void closeGroupModal({ clearResult: false });
        setHourlyIncomeMicrosits(0);
        setHeaderBalance(0);
        return false;
    }

    setHidden(authCard, true);
    setHidden(appHeader, false);
    updateHeaderHeightVar();

    const chats = state.chats || [];
    fillChatSwitch(chats, state.selected_chat_id);

    if (!chats.length) {
        activeSelectedChatId = null;
        groupEventKnownReminders = new Set();
        activeBuildingsOwnerUserId = null;
        lastIdleBuildings = [];
        setVisitMode(false);
        closeTransferModal();
        setActiveSidePanel(null);
        buildingsPanelAutoOpened = false;
        resetPlayersData();
        resetWebChatState();
        clearBuildingsPanel();
        clearSceneBuildings();
        closeChatModal();
        setGeyserProgress(0, 10, { visitBlockedByOwner: false });
        setWebSettings({
            hide_base: false,
            reject_geyser_catch_by_guest: false,
            notify_group_masturbation: true,
            notify_group_masturbation_sound: true,
        });
        setSettingsMenuOpen(false);
        clearGroupEventPolling();
        setGroupEventState(createDefaultGroupEventState(), { silent: true });
        void closeGroupModal({ clearResult: false });
        setHourlyIncomeMicrosits(0);
        setHeaderBalance(0);
        setLoadingMessage("Аккаунты не найдены в базе. Напишите боту в нужном чате и повторите вход.");
        return false;
    }

    if (state.selected_chat_id == null) {
        activeSelectedChatId = null;
        groupEventKnownReminders = new Set();
        activeBuildingsOwnerUserId = null;
        lastIdleBuildings = [];
        setVisitMode(false);
        closeTransferModal();
        setActiveSidePanel(null);
        buildingsPanelAutoOpened = false;
        resetPlayersData();
        resetWebChatState();
        clearBuildingsPanel();
        clearSceneBuildings();
        setGeyserProgress(0, 10, { visitBlockedByOwner: false });
        setWebSettings({
            hide_base: false,
            reject_geyser_catch_by_guest: false,
            notify_group_masturbation: true,
            notify_group_masturbation_sound: true,
        });
        setSettingsMenuOpen(false);
        clearGroupEventPolling();
        setGroupEventState(createDefaultGroupEventState(), { silent: true });
        void closeGroupModal({ clearResult: false });
        setHourlyIncomeMicrosits(0);
        setHeaderBalance(0);
        setLoadingMessage("");
        openChatModal(chats);
        return false;
    }

    const selectedChatId = Number(state.selected_chat_id);
    if (activeSelectedChatId !== null && Number(activeSelectedChatId) !== selectedChatId) {
        groupEventKnownReminders = new Set();
        dismissedGroupEventToken = null;
        setHourlyIncomeMicrosits(0);
        resetWebChatState();
    }
    if (activeSelectedChatId === null || Number(activeSelectedChatId) !== selectedChatId) {
        resetPlayersData();
    }

    closeChatModal();
    activeSelectedChatId = selectedChatId;
    const visitInfo = state && state.visit && state.visit.active ? state.visit : null;
    setVisitMode(
        Boolean(visitInfo),
        visitInfo ? Number(visitInfo.user_id) : null,
        visitInfo ? String(visitInfo.name || "") : "",
    );
    setWebSettings(state.web_settings || {
        hide_base: false,
        reject_geyser_catch_by_guest: false,
        notify_group_masturbation: true,
        notify_group_masturbation_sound: true,
    });
    setGeyserProgress(state.geyser_caught_today, state.geyser_daily_limit, {
        visitBlockedByOwner: Boolean(state.visit_geyser_blocked),
    });
    setGroupEventState(state.group_event || createDefaultGroupEventState(), { silent: true });
    setHeaderBalance(state.balance);
    if (!buildingsPanelAutoOpened) {
        if (!visitModeActive) {
            setActiveSidePanel("buildings");
        } else {
            setActiveSidePanel(null);
        }
        buildingsPanelAutoOpened = true;
    }
    if (visitModeActive) {
        setHidden(buildingsPanel, true);
    }
    if (buildingsPanelOpen && buildingsPanel) {
        setHidden(buildingsPanel, false);
    }
    const needInitialChatLoad = webChatLoadedChatId === null || Number(webChatLoadedChatId) !== selectedChatId;
    if (needInitialChatLoad) {
        void fetchWebChatMessages({ reset: true }).catch(() => {});
    }
    scheduleWebChatPolling();
    scheduleGroupEventPolling();
    updateHeaderHeightVar();
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
        void closeGroupModal({ clearResult: false });
        clearGroupEventPolling();
        setVisitMode(false);
        lastIdleBuildings = [];
        activeBuildingsOwnerUserId = null;
        setHourlyIncomeMicrosits(0);
        resetPlayersData();
        resetWebChatState();
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
            if (webChatPanelOpen) {
                await ensureWebChatLoaded({ force: true });
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
            if (webChatPanelOpen) {
                await ensureWebChatLoaded({ force: true });
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
            if (webChatPanelOpen) {
                await ensureWebChatLoaded({ force: true });
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

if (settingsBtn) {
    settingsBtn.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        if (activeSelectedChatId == null) {
            setSettingsMenuOpen(false);
            return;
        }
        setSettingsMenuOpen(!settingsMenuOpen);
    });
}

if (webChatToggleBtn) {
    webChatToggleBtn.addEventListener("click", () => {
        if (activeSelectedChatId == null) {
            setActiveSidePanel(null);
            return;
        }
        setActiveSidePanel(webChatPanelOpen ? null : "chat");
    });
}

if (chatPreviewBtn) {
    chatPreviewBtn.addEventListener("click", () => {
        if (activeSelectedChatId == null) {
            return;
        }
        setActiveSidePanel("chat");
    });
}

if (webChatInput) {
    webChatInput.addEventListener("input", () => {
        updateWebChatSubmitState();
    });
    webChatInput.addEventListener("keydown", (event) => {
        if (event.key === "Enter" && !event.shiftKey) {
            event.preventDefault();
            void submitWebChatMessage();
        }
    });
}

if (webChatForm) {
    webChatForm.addEventListener("submit", (event) => {
        event.preventDefault();
        void submitWebChatMessage();
    });
}

if (settingsMenu) {
    settingsMenu.addEventListener("click", async (event) => {
        event.stopPropagation();
        const row = event.target instanceof Element
            ? event.target.closest(".settings-item[data-setting-key]")
            : null;
        if (!row) {
            return;
        }
        const settingKey = row.dataset.settingKey;
        await toggleWebSetting(settingKey);
    });
    settingsMenu.addEventListener("keydown", async (event) => {
        const isActivateKey = event.key === "Enter" || event.key === " ";
        if (!isActivateKey) {
            return;
        }
        const row = event.target instanceof Element
            ? event.target.closest(".settings-item[data-setting-key]")
            : null;
        if (!row) {
            return;
        }
        event.preventDefault();
        event.stopPropagation();
        const settingKey = row.dataset.settingKey;
        await toggleWebSetting(settingKey);
    });
}

async function handleChatSwitchChange(rawValue, source) {
    const value = String(rawValue || "");
    if (source !== chatSwitch && chatSwitch) {
        chatSwitch.value = value;
    }
    if (source !== chatSwitchMobile && chatSwitchMobile) {
        chatSwitchMobile.value = value;
    }
    try {
        await selectChat(Number(value));
    } catch (error) {
        setLoadingMessage(error.message);
    }
}

if (chatSwitch) {
    chatSwitch.addEventListener("change", async (event) => {
        await handleChatSwitchChange(event.target.value, chatSwitch);
    });
}

if (chatSwitchMobile) {
    chatSwitchMobile.addEventListener("change", async (event) => {
        await handleChatSwitchChange(event.target.value, chatSwitchMobile);
    });
}

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
        renderTransferAmountUnit();
        updateTransferSubmitState();
        setTransferMessageNote();
    });

    transferAmountInput.addEventListener("paste", (event) => {
        event.preventDefault();
        const clipboardText = event.clipboardData ? event.clipboardData.getData("text") : "";
        const sanitized = normalizeTransferInput(clipboardText);
        const current = String(transferAmountInput.value || "");
        transferAmountInput.value = normalizeTransferInput(current + sanitized);
        renderTransferAmountUnit();
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

if (groupEventBanner) {
    groupEventBanner.addEventListener("click", (event) => {
        if (groupEventBannerClose && groupEventBannerClose.contains(event.target)) {
            return;
        }
        void openGroupModal();
    });
}

if (groupEventBannerClose) {
    groupEventBannerClose.addEventListener("click", (event) => {
        event.preventDefault();
        event.stopPropagation();
        const token = groupEventState && groupEventState.event_token ? String(groupEventState.event_token) : null;
        dismissedGroupEventToken = token;
        renderGroupEventBanner();
    });
}

if (groupModalCloseBtn) {
    groupModalCloseBtn.addEventListener("click", () => {
        void closeGroupModal();
    });
}

if (groupModalScrim) {
    groupModalScrim.addEventListener("click", () => {
        void closeGroupModal();
    });
}

if (groupModal) {
    groupModal.addEventListener("click", (event) => {
        if (event.target === groupModal) {
            void closeGroupModal();
        }
    });
}

window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && transferModalOpen) {
        closeTransferModal();
    }
    if (event.key === "Escape" && groupModalOpen) {
        void closeGroupModal();
    }
    if (event.key === "Escape" && settingsMenuOpen) {
        setSettingsMenuOpen(false);
    }
});

document.addEventListener("click", (event) => {
    if (!settingsMenuOpen || !settingsWrap) {
        return;
    }
    if (settingsWrap.contains(event.target)) {
        return;
    }
    setSettingsMenuOpen(false);
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

if (visitHomeBtn) {
    visitHomeBtn.addEventListener("click", async () => {
        if (!visitModeActive) {
            return;
        }
        try {
            await leaveVisit();
        } catch (error) {
            setLoadingMessage(error.message || "Не удалось вернуться домой");
        }
    });
}

if (settingsLogoutBtn) {
    settingsLogoutBtn.addEventListener("click", async () => {
        setSettingsMenuOpen(false);
        await fetch("/api/logout", { method: "POST", credentials: "include" });
        await refresh();
    });
}

window.addEventListener("resize", () => {
    updateHeaderHeightVar();
    if (!lastIdleBuildings.length) {
        return;
    }
    renderSceneBuildings(lastIdleBuildings);
});

closeTransferModal();
setWebSettings(webSettings);
setSettingsMenuOpen(false);
setGroupEventState(createDefaultGroupEventState(), { silent: true });
setGroupModalOpen(false);
updateHeaderHeightVar();
loadScene();
refresh();
