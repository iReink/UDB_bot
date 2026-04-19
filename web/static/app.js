const appHeader = document.getElementById("appHeader");
const authCard = document.getElementById("authCard");
const codeAuthHint = document.getElementById("codeAuthHint");
const authCodeInput = document.getElementById("authCodeInput");
const statusCard = document.getElementById("statusCard");
const statusText = document.getElementById("statusText");
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
const SCENE_LAYER_ORDER = ["sky", "sky_elements", "background", "foreground"];
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
    });
}

function renderScene(sceneConfig) {
    const layers = sceneConfig && typeof sceneConfig === "object" ? sceneConfig.layers : null;
    SCENE_LAYER_ORDER.forEach((layerName) => {
        const layerItems = layers && typeof layers === "object" ? layers[layerName] : [];
        renderSceneLayer(layerName, layerItems);
    });
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
    if (hidden) {
        element.classList.add("hidden");
    } else {
        element.classList.remove("hidden");
    }
}

function setLoadingMessage(message) {
    setHidden(statusCard, false);
    statusText.textContent = message;
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
        setHidden(statusCard, true);
        closeChatModal();
        setHidden(authCard, false);
        setHeaderBalance(0);
        return;
    }

    setHidden(authCard, true);
    setHidden(appHeader, false);
    setHidden(statusCard, false);

    const chats = state.chats || [];
    fillChatSwitch(chats, state.selected_chat_id);
    setHeaderBalance(state.balance);

    if (!chats.length) {
        closeChatModal();
        statusText.textContent = "Аккаунты не найдены в базе. Напишите боту в нужном чате и повторите вход.";
        return;
    }

    if (state.selected_chat_id == null) {
        setLoadingMessage("Выберите чат для продолжения.");
        openChatModal(chats);
        return;
    }

    closeChatModal();
    statusText.textContent = `Вы вошли через чат ${state.selected_chat_label}, ваш баланс ${state.balance} ${sitWord(state.balance)}`;
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
