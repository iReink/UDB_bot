const appHeader = document.getElementById("appHeader");
const authCard = document.getElementById("authCard");
const codeAuthHint = document.getElementById("codeAuthHint");
const authCodeInput = document.getElementById("authCodeInput");
const statusCard = document.getElementById("statusCard");
const statusText = document.getElementById("statusText");
const chatSwitch = document.getElementById("chatSwitch");
const logoutBtn = document.getElementById("logoutBtn");
const chatModal = document.getElementById("chatModal");
const chatList = document.getElementById("chatList");

let codeAuthInFlight = false;

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

refresh();
