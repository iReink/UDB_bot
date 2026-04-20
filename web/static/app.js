const appHeader = document.getElementById("appHeader");
const authCard = document.getElementById("authCard");
const codeAuthHint = document.getElementById("codeAuthHint");
const authCodeInput = document.getElementById("authCodeInput");
const chatBalanceLabel = document.getElementById("chatBalanceLabel");
const chatSwitch = document.getElementById("chatSwitch");
const logoutBtn = document.getElementById("logoutBtn");
const chatModal = document.getElementById("chatModal");
const chatList = document.getElementById("chatList");
const sceneLayerNodes = Array.from(document.querySelectorAll("[data-scene-layer]")).reduce((acc, node) => {
    const layerName = node.dataset.sceneLayer;
    if (layerName) {
        acc[layerName] = node;
    }
    return acc;
}, {});

let codeAuthInFlight = false;
let sceneAnimationFrameId = null;
const sceneAnimations = [];
const sceneSpawnIntervals = [];
const sceneSpawnStartupTimeouts = [];
const sceneSpawnCleanupTimeouts = [];
const sceneSpawnedElements = [];
const SCENE_LAYER_ORDER = ["sky", "sky_elements", "background", "foreground"];
const DEFAULT_SCENE_BASE_SIZE = { width: 1920, height: 1080 };
let sceneBaseSize = { ...DEFAULT_SCENE_BASE_SIZE };
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
    sceneSpawnIntervals.forEach((intervalId) => clearInterval(intervalId));
    sceneSpawnIntervals.length = 0;
    sceneSpawnStartupTimeouts.forEach((timeoutId) => clearTimeout(timeoutId));
    sceneSpawnStartupTimeouts.length = 0;
    sceneSpawnCleanupTimeouts.forEach((timeoutId) => clearTimeout(timeoutId));
    sceneSpawnCleanupTimeouts.length = 0;
    sceneSpawnedElements.forEach((element) => {
        if (element && element.parentNode) {
            element.parentNode.removeChild(element);
        }
    });
    sceneSpawnedElements.length = 0;
    sceneBaseSize = { ...DEFAULT_SCENE_BASE_SIZE };
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

function pickRandomScenePoint(points) {
    if (!Array.isArray(points) || !points.length) {
        return null;
    }
    const index = Math.floor(Math.random() * points.length);
    return points[index];
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
        const cleanupTimeoutId = window.setTimeout(() => {
            removeSceneSpawnedElement(image);
            const timeoutIndex = sceneSpawnCleanupTimeouts.indexOf(cleanupTimeoutId);
            if (timeoutIndex >= 0) {
                sceneSpawnCleanupTimeouts.splice(timeoutIndex, 1);
            }
        }, resolvedDisplayMs);
        sceneSpawnCleanupTimeouts.push(cleanupTimeoutId);
    };

    if (image.complete) {
        scheduleCleanup();
    } else {
        image.addEventListener("load", scheduleCleanup, { once: true });
    }

    image.addEventListener("error", () => {
        removeSceneSpawnedElement(image);
    }, { once: true });
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

        const kind = (item.kind || "image").toLowerCase();
        if (kind !== "image") {
            return;
        }

        if (!item.src) {
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

function sitWord(amount) {
    const n = Math.abs(Number(amount) || 0);
    if (n % 10 === 1 && n % 100 !== 11) {
        return "сит";
    }
    return "сита";
}

function setHeaderBalance(amount) {
    if (!chatBalanceLabel) {
        return;
    }
    const value = Number.isFinite(Number(amount)) ? Number(amount) : 0;
    chatBalanceLabel.textContent = `${value} ${sitWord(value)}`;
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

function setLoadingMessage(message) {
    if (codeAuthHint && !authCard.classList.contains("hidden")) {
        codeAuthHint.textContent = message;
        return;
    }
    if (message) {
        console.error(message);
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
        setHidden(appHeader, true);
        closeChatModal();
        setHidden(authCard, false);
        setHeaderBalance(0);
        return;
    }

    setHidden(authCard, true);
    setHidden(appHeader, false);

    const chats = state.chats || [];
    fillChatSwitch(chats, state.selected_chat_id);
    setHeaderBalance(state.balance);

    if (!chats.length) {
        closeChatModal();
        setLoadingMessage("Аккаунты не найдены в базе. Напишите боту в нужном чате и повторите вход.");
        return;
    }

    if (state.selected_chat_id == null) {
        setLoadingMessage("");
        openChatModal(chats);
        return;
    }

    closeChatModal();
}

async function fetchState() {
    const response = await fetch("/api/state", { credentials: "include" });
    if (!response.ok) {
        throw new Error("Не удалось получить состояние");
    }
    return response.json();
}

async function selectChat(chatId) {
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
    renderState(state);
}

async function refresh() {
    try {
        const state = await fetchState();
        renderState(state);
    } catch (_error) {
        setLoadingMessage("Ошибка загрузки страницы.");
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
        renderState(state);
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

logoutBtn.addEventListener("click", async () => {
    await fetch("/api/logout", { method: "POST", credentials: "include" });
    await refresh();
});

loadScene();
refresh();
