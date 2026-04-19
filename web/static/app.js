const BOT_USERNAME = window.__BOT_USERNAME__ || "";

const appHeader = document.getElementById("appHeader");
const authCard = document.getElementById("authCard");
const authHint = document.getElementById("authHint");
const statusCard = document.getElementById("statusCard");
const statusText = document.getElementById("statusText");
const chatSwitch = document.getElementById("chatSwitch");
const logoutBtn = document.getElementById("logoutBtn");
const chatModal = document.getElementById("chatModal");
const chatList = document.getElementById("chatList");
const tgAuthWidget = document.getElementById("tgAuthWidget");

let widgetRendered = false;

function hostLooksLikeIp(host) {
    return /^(?:\d{1,3}\.){3}\d{1,3}$/.test(host);
}

function getTelegramDomainHint() {
    const host = window.location.hostname;
    const hints = [];

    if (hostLooksLikeIp(host)) {
        hints.push("Открыт IP-адрес. Для Telegram Login нужен домен.");
    }

    const isLocalhost = host === "localhost" || host === "127.0.0.1";
    if (!isLocalhost && window.location.protocol !== "https:") {
        hints.push("Для Telegram Login нужен HTTPS.");
    }

    hints.push("Проверьте в BotFather: /setdomain -> ваш домен.");
    return hints.join(" ");
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

function renderAuthWidget() {
    if (widgetRendered) {
        return;
    }
    tgAuthWidget.innerHTML = "";
    if (!BOT_USERNAME) {
        authHint.textContent = "На сервере не задан BOT_USERNAME, виджет авторизации недоступен.";
        return;
    }
    authHint.textContent = getTelegramDomainHint();

    const script = document.createElement("script");
    script.async = true;
    script.src = "https://telegram.org/js/telegram-widget.js?22";
    script.setAttribute("data-telegram-login", BOT_USERNAME);
    script.setAttribute("data-size", "large");
    script.setAttribute("data-userpic", "false");
    script.setAttribute("data-request-access", "write");
    script.setAttribute("data-onauth", "onTelegramAuth(user)");
    tgAuthWidget.appendChild(script);
    widgetRendered = true;
}

function renderState(state) {
    if (!state.authorized) {
        setHidden(appHeader, true);
        setHidden(statusCard, true);
        closeChatModal();
        setHidden(authCard, false);
        renderAuthWidget();
        return;
    }

    setHidden(authCard, true);
    setHidden(appHeader, false);
    setHidden(statusCard, false);

    const chats = state.chats || [];
    fillChatSwitch(chats, state.selected_chat_id);

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
    statusText.textContent = `Вы вошли через чат ${state.selected_chat_label}, ваш баланс ${state.balance} сит`;
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
    } catch (error) {
        setLoadingMessage("Ошибка загрузки страницы.");
    }
}

window.onTelegramAuth = async function onTelegramAuth(user) {
    try {
        const response = await fetch("/api/auth/telegram", {
            method: "POST",
            credentials: "include",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ auth_data: user }),
        });
        if (!response.ok) {
            const payload = await response.json().catch(() => ({}));
            throw new Error(payload.detail || "Ошибка авторизации");
        }
        const state = await response.json();
        renderState(state);
    } catch (error) {
        authHint.textContent = error.message;
    }
};

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

refresh();
