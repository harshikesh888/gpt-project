/* Jarvis AI — chat client
   Vanilla JS, local assets only. Renders model Markdown through DOMPurify,
   streams with backpressure-aware SSE parsing, and keeps history in
   localStorage. */

"use strict";

(() => {
    const STORAGE_KEY = "jarvis.chats.v2";
    const THEME_KEY = "jarvis.theme.v2";
    const MODE_KEY = "jarvis.mode.v2";
    const MAX_CONVERSATIONS = 30;
    const MAX_STORED_MESSAGES = 80;
    const MAX_STORED_CHARS = 24_000;
    const MAX_HISTORY_CHARS = 12_000;
    const STREAM_RENDER_INTERVAL = 70;
    const FIRST_TOKEN_HINT_MS = 12_000;
    const WATCHDOG_MS = 100_000;

    const DEFAULT_CONFIG = {
        app_name: "Jarvis AI",
        version: "2.0.0",
        ready: true,
        docs_enabled: true,
        max_message_chars: 4000,
        history_limit: 12,
        modes: [],
        privacy: "",
    };

    const state = {
        config: { ...DEFAULT_CONFIG },
        conversations: [],
        activeId: null,
        mode: "fast",
        theme: "auto",
        streaming: null,
        cooldownUntil: 0,
        cooldownTimer: 0,
        filter: "",
        storageWarned: false,
        els: {},
    };

    const isFinePointer = window.matchMedia("(pointer: fine)").matches;
    const prefersDark = window.matchMedia("(prefers-color-scheme: dark)");

    let highlightLoader = null;

    class ApiError extends Error {
        constructor(message, details = {}) {
            super(message);
            this.name = "ApiError";
            this.status = details.status || 0;
            this.code = details.code || "";
            this.requestId = details.requestId || "";
            this.retryAfter = details.retryAfter || 0;
        }
    }

    /* ---------------------------------------------------------------------
       Utilities
       --------------------------------------------------------------------- */

    const $ = (selector, root = document) => root.querySelector(selector);
    const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = text;
        return node;
    }

    function icon(name, className = "icon") {
        const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
        svg.setAttribute("class", className);
        svg.setAttribute("aria-hidden", "true");
        const use = document.createElementNS("http://www.w3.org/2000/svg", "use");
        use.setAttribute("href", `#i-${name}`);
        svg.appendChild(use);
        return svg;
    }

    function uid() {
        if (window.crypto && typeof window.crypto.randomUUID === "function") {
            return window.crypto.randomUUID();
        }
        return `m-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
    }

    function clamp(value, min, max) {
        return Math.min(Math.max(value, min), max);
    }

    function escapeHtml(text) {
        const replacements = {
            "&": "&amp;",
            "<": "&lt;",
            ">": "&gt;",
            '"': "&quot;",
            "'": "&#39;",
        };
        return String(text).replace(/[&<>"']/g, (character) => replacements[character]);
    }

    function safeStorageGet(key) {
        try {
            return window.localStorage.getItem(key);
        } catch {
            return null;
        }
    }

    function safeStorageSet(key, value) {
        try {
            window.localStorage.setItem(key, value);
            return true;
        } catch {
            if (!state.storageWarned) {
                state.storageWarned = true;
                toast("Local storage is unavailable, so chats won't persist.", { type: "error" });
            }
            return false;
        }
    }

    function modeLabel(mode) {
        return mode === "smart" ? "Deep" : "Fast";
    }

    function appInitial() {
        const name = String(state.config.app_name || "AI").trim();
        return (name.charAt(0) || "A").toUpperCase();
    }

    function titleFromText(text) {
        const cleaned = String(text)
            .replace(/```[\s\S]*?```/g, " code ")
            .replace(/[#*_>`~[\]()]/g, " ")
            .replace(/\s+/g, " ")
            .trim();
        const title = cleaned || "New chat";
        return title.length > 52 ? `${title.slice(0, 51).trimEnd()}…` : title;
    }

    function formatLatency(ms, cached) {
        if (cached) return "Instant";
        if (typeof ms !== "number" || !Number.isFinite(ms)) return "";
        if (ms < 1000) return `${Math.max(1, Math.round(ms))} ms`;
        return `${(ms / 1000).toFixed(1)} s`;
    }

    function formatRelative(timestamp) {
        const seconds = Math.max(0, Math.round((Date.now() - timestamp) / 1000));
        if (seconds < 60) return "now";
        const minutes = Math.round(seconds / 60);
        if (minutes < 60) return `${minutes}m`;
        const hours = Math.round(minutes / 60);
        if (hours < 24) return `${hours}h`;
        const days = Math.round(hours / 24);
        if (days < 7) return `${days}d`;
        return new Date(timestamp).toLocaleDateString(undefined, { month: "short", day: "numeric" });
    }

    function isNearBottom() {
        const node = state.els.messages;
        return node.scrollHeight - node.scrollTop - node.clientHeight < 130;
    }

    function maybeScroll(force = false) {
        if (force || isNearBottom()) {
            state.els.messages.scrollTop = state.els.messages.scrollHeight;
            state.els.scrollBottom.hidden = true;
        }
    }

    /* ---------------------------------------------------------------------
       Markdown
       --------------------------------------------------------------------- */

    const SANITIZE_CONFIG = {
        USE_PROFILES: { html: true },
        FORBID_TAGS: [
            "img",
            "iframe",
            "object",
            "embed",
            "form",
            "input",
            "textarea",
            "select",
            "option",
            "button",
            "style",
            "link",
            "meta",
            "base",
            "video",
            "audio",
            "source",
        ],
        FORBID_ATTR: ["style", "srcset", "formaction"],
        ALLOW_DATA_ATTR: false,
    };

    function plainTextHtml(text) {
        return escapeHtml(text).replace(/\n/g, "<br>");
    }

    function renderMarkdown(text) {
        if (!text) return "";
        let html;
        try {
            if (window.marked && typeof window.marked.parse === "function") {
                html = window.marked.parse(text, { gfm: true, breaks: true });
            } else {
                return plainTextHtml(text);
            }
        } catch {
            return plainTextHtml(text);
        }
        if (!window.DOMPurify || typeof window.DOMPurify.sanitize !== "function") {
            // Never inject unsanitised HTML if the sanitizer failed to load.
            return plainTextHtml(text);
        }
        try {
            return window.DOMPurify.sanitize(html, SANITIZE_CONFIG);
        } catch {
            return plainTextHtml(text);
        }
    }

    function setRichHtml(container, text) {
        container.innerHTML = renderMarkdown(text);
        $$("a", container).forEach((link) => {
            link.target = "_blank";
            link.rel = "noopener noreferrer nofollow";
        });
    }

    function createStreamRenderer(rich) {
        return { rich, raf: 0, last: 0, text: "" };
    }

    function scheduleStreamRender(renderer, text) {
        if (!renderer || !renderer.rich) return;
        renderer.text = text;
        if (renderer.raf) return;

        const tick = () => {
            if (!renderer.rich || !renderer.rich.isConnected) {
                renderer.raf = 0;
                return;
            }
            const now = performance.now();
            if (now - renderer.last < STREAM_RENDER_INTERVAL) {
                renderer.raf = requestAnimationFrame(tick);
                return;
            }
            setRichHtml(renderer.rich, renderer.text);
            renderer.last = now;
            renderer.raf = 0;
            maybeScroll();
        };
        renderer.raf = requestAnimationFrame(tick);
    }

    function cancelStreamRender(renderer) {
        if (!renderer) return;
        if (renderer.raf) cancelAnimationFrame(renderer.raf);
        renderer.raf = 0;
        renderer.text = "";
        renderer.rich = null;
    }

    function loadHighlighter() {
        if (window.hljs) return Promise.resolve(window.hljs);
        if (highlightLoader) return highlightLoader;
        const version = document.body.dataset.assetVersion || "1";
        highlightLoader = new Promise((resolve, reject) => {
            const script = document.createElement("script");
            script.src = `/static/vendor/highlight.min.js?v=${encodeURIComponent(version)}`;
            script.onload = () => resolve(window.hljs);
            script.onerror = () => reject(new Error("Highlighter failed to load"));
            document.head.appendChild(script);
        }).catch((error) => {
            highlightLoader = null;
            throw error;
        });
        return highlightLoader;
    }

    function addCodeCopyButtons(container) {
        $$("pre", container).forEach((pre) => {
            if (pre.querySelector(".code-copy")) return;
            const button = el("button", "code-copy", "Copy");
            button.type = "button";
            button.setAttribute("aria-label", "Copy code");
            button.addEventListener("click", async (event) => {
                event.preventDefault();
                const code = pre.querySelector("code");
                const copied = await copyText(code ? code.innerText : pre.innerText, { quiet: true });
                if (copied) {
                    button.textContent = "Copied";
                    window.setTimeout(() => {
                        button.textContent = "Copy";
                    }, 1400);
                }
            });
            pre.appendChild(button);
        });
    }

    async function highlightCode(container) {
        const blocks = $$("pre code", container);
        if (!blocks.length) return;
        try {
            const hljs = await loadHighlighter();
            if (!hljs) return;
            blocks.slice(0, 24).forEach((block) => {
                try {
                    hljs.highlightElement(block);
                } catch {
                    /* A malformed block should never break the message. */
                }
            });
        } catch {
            /* Highlighting is an enhancement, not a requirement. */
        } finally {
            addCodeCopyButtons(container);
        }
    }

    /* ---------------------------------------------------------------------
       Toasts and clipboard
       --------------------------------------------------------------------- */

    function toast(message, options = {}) {
        const {
            type = "info",
            actionLabel = "",
            onAction = null,
            duration = 3400,
        } = options;

        const node = el("div", `toast${type === "error" ? " is-error" : ""}`);
        node.appendChild(icon(type === "error" ? "alert" : "check"));
        node.appendChild(el("span", "toast-message", message));

        let timer = 0;
        const dismiss = () => {
            window.clearTimeout(timer);
            node.classList.add("is-leaving");
            window.setTimeout(() => node.remove(), 180);
        };

        if (actionLabel && onAction) {
            const action = el("button", "", actionLabel);
            action.type = "button";
            action.addEventListener("click", () => {
                dismiss();
                onAction();
            });
            node.appendChild(action);
        }

        const host = state.els.toasts;
        host.appendChild(node);
        while (host.children.length > 3) host.firstElementChild.remove();
        timer = window.setTimeout(dismiss, duration);
    }

    async function copyText(text, { quiet = false } = {}) {
        if (!text) return false;
        try {
            if (navigator.clipboard && window.isSecureContext) {
                await navigator.clipboard.writeText(text);
            } else {
                const area = el("textarea");
                area.value = text;
                area.setAttribute("readonly", "");
                area.style.position = "fixed";
                area.style.opacity = "0";
                document.body.appendChild(area);
                area.select();
                const ok = document.execCommand("copy");
                area.remove();
                if (!ok) throw new Error("Copy failed");
            }
            if (!quiet) toast("Copied to clipboard");
            return true;
        } catch {
            if (!quiet) toast("Could not copy to clipboard.", { type: "error" });
            return false;
        }
    }

    /* ---------------------------------------------------------------------
       Persistence
       --------------------------------------------------------------------- */

    function normaliseMessage(raw) {
        if (!raw || typeof raw !== "object") return null;
        const role = raw.role === "assistant" ? "assistant" : raw.role === "user" ? "user" : null;
        if (!role) return null;
        const content = typeof raw.content === "string" ? raw.content.slice(0, MAX_STORED_CHARS) : "";
        const id = typeof raw.id === "string" && raw.id ? raw.id : uid();
        return {
            id,
            role,
            content,
            ts: Number(raw.ts) || Date.now(),
            mode: raw.mode === "smart" ? "smart" : "fast",
            model: typeof raw.model === "string" ? raw.model : "",
            elapsed_ms: typeof raw.elapsed_ms === "number" ? raw.elapsed_ms : null,
            ttft_ms: typeof raw.ttft_ms === "number" ? raw.ttft_ms : null,
            cached: Boolean(raw.cached),
            error: typeof raw.error === "string" ? raw.error : null,
            requestId: typeof raw.requestId === "string" ? raw.requestId : "",
            stopped: Boolean(raw.stopped),
        };
    }

    function normaliseConversation(raw) {
        if (!raw || typeof raw !== "object") return null;
        const messages = Array.isArray(raw.messages)
            ? raw.messages
                .map(normaliseMessage)
                .filter((message) => {
                    if (!message) return false;
                    return message.role === "user" || message.content || message.error || message.stopped;
                })
                .slice(-MAX_STORED_MESSAGES)
            : [];
        const firstUser = messages.find((message) => message.role === "user");
        const mode = raw.mode === "smart" ? "smart" : "fast";
        return {
            id: typeof raw.id === "string" && raw.id ? raw.id : uid(),
            title: typeof raw.title === "string" && raw.title.trim()
                ? raw.title.slice(0, 90)
                : firstUser
                    ? titleFromText(firstUser.content)
                    : "New chat",
            createdAt: Number(raw.createdAt) || Date.now(),
            updatedAt: Number(raw.updatedAt) || Date.now(),
            mode,
            messages,
        };
    }

    function loadStore() {
        state.conversations = [];
        state.activeId = null;

        const raw = safeStorageGet(STORAGE_KEY);
        if (!raw) return;

        let data;
        try {
            data = JSON.parse(raw);
        } catch {
            return;
        }
        if (!data || data.version !== 2 || !Array.isArray(data.conversations)) return;

        const seen = new Set();
        state.conversations = data.conversations
            .map(normaliseConversation)
            .filter((conversation) => {
                if (!conversation || seen.has(conversation.id)) return false;
                seen.add(conversation.id);
                return true;
            })
            .sort((a, b) => b.updatedAt - a.updatedAt)
            .slice(0, MAX_CONVERSATIONS);

        const hasActive = typeof data.activeId === "string"
            && state.conversations.some((conversation) => conversation.id === data.activeId);
        if (hasActive) {
            state.activeId = data.activeId;
        } else if (data.activeId === null) {
            state.activeId = null;
        } else {
            state.activeId = state.conversations[0]?.id || null;
        }
    }

    function trimConversation(conversation) {
        const messages = conversation.messages
            .slice(-MAX_STORED_MESSAGES)
            .map((message) => ({
                ...message,
                content: message.content.slice(0, MAX_STORED_CHARS),
                error: message.error || null,
            }));
        return { ...conversation, messages };
    }

    function saveStore() {
        const payload = {
            version: 2,
            activeId: state.activeId,
            conversations: state.conversations
                .slice(0, MAX_CONVERSATIONS)
                .map(trimConversation),
        };
        let serialised = "";
        try {
            serialised = JSON.stringify(payload);
        } catch {
            return;
        }

        if (safeStorageSet(STORAGE_KEY, serialised)) return;

        // Quota exceeded: drop the oldest conversations and retry a few times.
        const oldestFirst = [...state.conversations].sort((a, b) => a.updatedAt - b.updatedAt);
        for (let attempt = 0; attempt < 6 && oldestFirst.length > 1; attempt += 1) {
            const removed = oldestFirst.shift();
            state.conversations = state.conversations.filter((item) => item.id !== removed.id);
            if (state.activeId === removed.id) state.activeId = state.conversations[0]?.id || null;
            try {
                const retry = JSON.stringify({
                    version: 2,
                    activeId: state.activeId,
                    conversations: state.conversations.map(trimConversation),
                });
                if (safeStorageSet(STORAGE_KEY, retry)) return;
            } catch {
                /* keep trying */
            }
        }
    }

    /* ---------------------------------------------------------------------
       Conversation model
       --------------------------------------------------------------------- */

    function getActiveConversation() {
        return state.conversations.find((conversation) => conversation.id === state.activeId) || null;
    }

    function createConversation(mode = state.mode) {
        const now = Date.now();
        return {
            id: uid(),
            title: "New chat",
            createdAt: now,
            updatedAt: now,
            mode,
            messages: [],
        };
    }

    function ensureConversation() {
        const existing = getActiveConversation();
        if (existing) return existing;

        const conversation = createConversation(state.mode);
        state.conversations.unshift(conversation);
        state.activeId = conversation.id;
        return conversation;
    }

    function removeMessage(conversation, messageId) {
        conversation.messages = conversation.messages.filter((message) => message.id !== messageId);
    }

    function buildHistory(conversation, currentUser, assistantId) {
        const limit = clamp(Number(state.config.history_limit) || 12, 0, 24);
        if (!limit) return [];

        const selected = [];
        let used = 0;

        for (let index = conversation.messages.length - 1; index >= 0; index -= 1) {
            const message = conversation.messages[index];
            if (message.id === currentUser.id || message.id === assistantId) continue;
            if (message.role === "assistant" && message.error) continue;
            if (!message.content) continue;

            const content = message.content.slice(0, 8000);
            if (selected.length >= limit || used + content.length > MAX_HISTORY_CHARS) break;
            selected.unshift({ role: message.role, content });
            used += content.length;
        }

        return selected;
    }

    /* ---------------------------------------------------------------------
       Rendering: sidebar
       --------------------------------------------------------------------- */

    function renderSidebar() {
        const list = state.els.conversationList;
        list.replaceChildren();

        state.els.searchWrap.hidden = state.conversations.length < 5 && !state.filter;

        if (!state.conversations.length) {
            const empty = el("div", "empty-chats");
            empty.appendChild(icon("message"));
            empty.appendChild(el("span", "", "Your chats will appear here"));
            list.appendChild(empty);
            return;
        }

        const filter = state.filter.trim().toLowerCase();
        const conversations = [...state.conversations]
            .sort((a, b) => b.updatedAt - a.updatedAt)
            .filter((conversation) => !filter || conversation.title.toLowerCase().includes(filter));

        list.appendChild(el("div", "list-label", filter ? "Results" : "Recent"));

        if (!conversations.length) {
            const empty = el("div", "empty-chats");
            empty.appendChild(icon("search"));
            empty.appendChild(el("span", "", "No matching chats"));
            list.appendChild(empty);
            return;
        }

        const fragment = document.createDocumentFragment();
        conversations.forEach((conversation) => {
            const row = el("div", `conversation-row${conversation.id === state.activeId ? " is-active" : ""}`);
            const select = el("button", "conversation-select");
            select.type = "button";
            select.title = `${conversation.title} · ${formatRelative(conversation.updatedAt)}`;
            select.setAttribute("aria-current", conversation.id === state.activeId ? "true" : "false");
            select.appendChild(icon("message"));
            select.appendChild(el("span", "conversation-title", conversation.title));
            select.addEventListener("click", () => selectConversation(conversation.id));

            const remove = el("button", "conversation-delete");
            remove.type = "button";
            remove.setAttribute("aria-label", `Delete chat: ${conversation.title}`);
            remove.title = "Delete chat";
            remove.appendChild(icon("trash"));
            remove.addEventListener("click", (event) => {
                event.stopPropagation();
                deleteConversation(conversation.id);
            });

            row.append(select, remove);
            fragment.appendChild(row);
        });
        list.appendChild(fragment);

        const active = list.querySelector(".conversation-row.is-active");
        if (active) active.scrollIntoView({ block: "nearest" });
    }

    function selectConversation(id) {
        if (state.streaming) stopStreaming(true);
        const conversation = state.conversations.find((item) => item.id === id);
        if (!conversation) return;
        state.activeId = id;
        state.mode = conversation.mode;
        state.filter = "";
        state.els.searchChats.value = "";
        saveStore();
        renderSidebar();
        renderActiveConversation();
        updateModeUI();
        closeSidebar(false);
        if (isFinePointer) state.els.prompt.focus();
    }

    function deleteConversation(id) {
        const index = state.conversations.findIndex((conversation) => conversation.id === id);
        if (index < 0) return;
        if (state.streaming && state.streaming.conversationId === id) stopStreaming(true);

        const [removed] = state.conversations.splice(index, 1);
        if (state.activeId === id) {
            const next = [...state.conversations].sort((a, b) => b.updatedAt - a.updatedAt)[0];
            state.activeId = next?.id || null;
            if (next) state.mode = next.mode;
            renderActiveConversation();
        }
        saveStore();
        renderSidebar();
        updateHeader();

        toast("Chat deleted", {
            actionLabel: "Undo",
            onAction: () => {
                const restoreAt = Math.min(index, state.conversations.length);
                state.conversations.splice(restoreAt, 0, removed);
                state.activeId = removed.id;
                state.mode = removed.mode;
                saveStore();
                renderSidebar();
                renderActiveConversation();
                updateModeUI();
            },
        });
    }

    function newChat() {
        if (state.streaming) stopStreaming(true);
        state.activeId = null;
        state.filter = "";
        state.els.searchChats.value = "";
        saveStore();
        renderSidebar();
        renderActiveConversation();
        updateHeader();
        closeSidebar(false);
        if (isFinePointer) state.els.prompt.focus();
    }

    /* ---------------------------------------------------------------------
       Rendering: messages
       --------------------------------------------------------------------- */

    function hideWelcome() {
        const welcome = state.els.messages.querySelector(".welcome");
        if (welcome) welcome.remove();
    }

    function showWelcome() {
        state.els.messages.setAttribute("aria-busy", "false");
        state.els.messages.replaceChildren();
        const template = $("#welcomeTemplate");
        if (template) state.els.messages.appendChild(template.content.cloneNode(true));
    }

    function makeCopyButton(textProvider) {
        const button = el("button", "icon-action");
        button.type = "button";
        button.title = "Copy message";
        button.setAttribute("aria-label", "Copy message");
        button.appendChild(icon("copy"));
        button.addEventListener("click", () => {
            const text = textProvider();
            if (text) void copyText(text);
        });
        return button;
    }

    function makeRegenerateButton(messageId) {
        const button = el("button", "icon-action");
        button.type = "button";
        button.title = "Regenerate response";
        button.setAttribute("aria-label", "Regenerate response");
        button.appendChild(icon("refresh"));
        button.addEventListener("click", () => regenerate(messageId));
        return button;
    }

    function makeErrorBlock(message, isLast) {
        const block = el("div", "message-error");
        block.appendChild(icon("alert"));
        const body = el("div", "message-error-body");
        body.appendChild(el("span", "", message.error || "Something went wrong."));
        if (message.requestId) {
            body.appendChild(el("small", "", `Ref: ${message.requestId}`));
        }
        if (isLast) {
            const retry = el("button", "", "Try again");
            retry.type = "button";
            retry.addEventListener("click", () => regenerate(message.id));
            body.appendChild(retry);
        }
        block.appendChild(body);
        return block;
    }

    function createMessageElement(message, conversation, options = {}) {
        const { isLast = false } = options;
        const isAssistant = message.role === "assistant";
        const article = el("article", `message is-${isAssistant ? "assistant" : "user"}`);
        article.dataset.messageId = message.id;

        if (!isAssistant) {
            const body = el("div", "message-body");
            const bubble = el("div", "user-bubble", message.content);
            body.appendChild(bubble);
            const actions = el("div", "message-actions");
            actions.appendChild(makeCopyButton(() => message.content));
            body.appendChild(actions);
            article.appendChild(body);
            return { article, rich: null, thinking: null, badge: null, latency: null };
        }

        const avatar = el("div", "avatar", appInitial());
        avatar.setAttribute("aria-hidden", "true");

        const body = el("div", "message-body");
        const meta = el("div", "message-meta");
        meta.appendChild(el("span", "author", "Jarvis"));

        const badge = el("span", "message-badge", modeLabel(message.mode || conversation.mode));
        if (message.model) badge.title = `Model: ${message.model}`;
        meta.appendChild(badge);

        const latency = el("span", "message-latency");
        if (message.cached) {
            latency.textContent = "Instant";
            latency.title = "Served from the response cache";
        } else if (typeof message.elapsed_ms === "number" && message.elapsed_ms > 0) {
            latency.textContent = formatLatency(message.elapsed_ms, false);
            latency.title = message.ttft_ms
                ? `First token in ${Math.round(message.ttft_ms)} ms`
                : "Response time";
        }
        meta.appendChild(latency);

        const actions = el("div", "message-actions");
        actions.appendChild(makeCopyButton(() => message.content));
        if (isLast && !state.streaming && !message.error) {
            actions.appendChild(makeRegenerateButton(message.id));
        }
        meta.appendChild(actions);

        const thinking = el("div", "thinking");
        const dots = el("span", "thinking-dots");
        dots.append(el("span"), el("span"), el("span"));
        const thinkingLabel = el("span", "", message.mode === "smart" ? "Reasoning…" : "Thinking…");
        thinking.append(dots, thinkingLabel);
        thinking.hidden = Boolean(message.content) || Boolean(message.error);

        const rich = el("div", "rich");
        if (message.content) setRichHtml(rich, message.content);

        body.append(meta, thinking, rich);

        if (message.stopped && !message.error) {
            body.appendChild(el("p", "stopped-note", "Generation stopped."));
        }
        if (message.error) {
            body.appendChild(makeErrorBlock(message, isLast));
        }

        article.append(avatar, body);
        return { article, rich, thinking, thinkingLabel, badge, latency };
    }

    function appendMessage(message, conversation, options = {}) {
        hideWelcome();
        const refs = createMessageElement(message, conversation, options);
        state.els.messages.appendChild(refs.article);
        return refs;
    }

    function isLastAssistantMessage(conversation, message) {
        if (!conversation.messages.length) return false;
        const last = conversation.messages[conversation.messages.length - 1];
        return last.id === message.id && last.role === "assistant";
    }

    function renderActiveConversation() {
        if (state.streaming) return;
        state.els.messages.setAttribute("aria-busy", "false");

        const conversation = getActiveConversation();
        if (!conversation || !conversation.messages.length) {
            showWelcome();
            updateHeader();
            updateModeUI();
            return;
        }

        state.mode = conversation.mode;
        state.els.messages.replaceChildren();

        conversation.messages.forEach((message) => {
            const isLast = isLastAssistantMessage(conversation, message);
            const refs = createMessageElement(message, conversation, { isLast });
            state.els.messages.appendChild(refs.article);
            if (message.role === "assistant" && message.content && refs.rich) {
                void highlightCode(refs.rich);
            }
        });

        updateHeader();
        updateModeUI();
        requestAnimationFrame(() => {
            state.els.messages.scrollTop = state.els.messages.scrollHeight;
        });
    }

    function updateHeader() {
        const conversation = getActiveConversation();
        const title = conversation ? conversation.title : "New chat";
        state.els.chatTitle.textContent = title;
        document.title = conversation ? `${title} — ${state.config.app_name}` : state.config.app_name;

        if (state.streaming) {
            state.els.chatSubtitle.textContent = "Generating response…";
        } else if (!state.config.ready) {
            state.els.chatSubtitle.textContent = "Temporarily unavailable";
        } else if (conversation) {
            const count = conversation.messages.filter((message) => message.role === "user").length;
            state.els.chatSubtitle.textContent = `${count} ${count === 1 ? "message" : "messages"}`;
        } else {
            state.els.chatSubtitle.textContent = "Private by design";
        }
    }

    /* ---------------------------------------------------------------------
       Mode / theme
       --------------------------------------------------------------------- */

    function updateModeUI() {
        $$("[data-mode]").forEach((button) => {
            const active = button.dataset.mode === state.mode;
            button.classList.toggle("is-active", active);
            button.setAttribute("aria-pressed", active ? "true" : "false");
        });
        updateComposer();
    }

    function setMode(mode) {
        if (!["fast", "smart"].includes(mode)) return;
        state.mode = mode;
        safeStorageSet(MODE_KEY, mode);
        const conversation = getActiveConversation();
        if (conversation) {
            conversation.mode = mode;
            saveStore();
        }
        updateModeUI();
    }

    function effectiveTheme() {
        if (state.theme === "dark" || state.theme === "light") return state.theme;
        return prefersDark.matches ? "dark" : "light";
    }

    function applyTheme() {
        const effective = effectiveTheme();
        if (state.theme === "auto") {
            document.documentElement.removeAttribute("data-theme");
        } else {
            document.documentElement.setAttribute("data-theme", state.theme);
        }
        state.els.themeLabel.textContent = effective === "dark" ? "Dark" : "Light";
        state.els.themeToggle.setAttribute(
            "aria-label",
            effective === "dark" ? "Switch to light theme" : "Switch to dark theme"
        );
        const use = state.els.themeToggle.querySelector("use");
        if (use) use.setAttribute("href", effective === "dark" ? "#i-sun" : "#i-moon");
        $$('meta[name="theme-color"]').forEach((meta) => {
            meta.setAttribute("content", effective === "dark" ? "#0b0d12" : "#f7f8fb");
        });
    }

    function toggleTheme() {
        state.theme = effectiveTheme() === "dark" ? "light" : "dark";
        safeStorageSet(THEME_KEY, state.theme);
        applyTheme();
    }

    /* ---------------------------------------------------------------------
       Composer
       --------------------------------------------------------------------- */

    function resizePrompt() {
        const prompt = state.els.prompt;
        prompt.style.height = "auto";
        prompt.style.height = `${Math.min(prompt.scrollHeight, 200)}px`;
    }

    function updateCharCount() {
        const length = state.els.prompt.value.length;
        const max = state.config.max_message_chars;
        state.els.charCount.textContent = `${length}/${max}`;
        state.els.charCount.hidden = length < max * 0.8;
        state.els.charCount.classList.toggle("is-warn", length > max * 0.95);
    }

    function updateComposer() {
        const streaming = Boolean(state.streaming);
        const cooling = Date.now() < state.cooldownUntil;
        const ready = Boolean(state.config.ready);
        const hasText = state.els.prompt.value.trim().length > 0;

        state.els.sendBtn.disabled = streaming || cooling || !ready || !hasText;
        state.els.sendBtn.hidden = streaming;
        state.els.stopBtn.hidden = !streaming;
        state.els.prompt.disabled = !ready;

        if (streaming) {
            state.els.modeHint.textContent = "Generating…";
        } else if (cooling) {
            const seconds = Math.max(1, Math.ceil((state.cooldownUntil - Date.now()) / 1000));
            state.els.modeHint.textContent = `Rate limited · ${seconds}s`;
        } else {
            state.els.modeHint.textContent = state.mode === "smart" ? "Deep reasoning" : "Fast answers";
        }
        updateCharCount();
    }

    function startCooldown(seconds) {
        const duration = clamp(Number(seconds) || 30, 1, 3600);
        state.cooldownUntil = Date.now() + duration * 1000;
        window.clearInterval(state.cooldownTimer);
        state.cooldownTimer = window.setInterval(() => {
            if (Date.now() >= state.cooldownUntil) {
                window.clearInterval(state.cooldownTimer);
                state.cooldownTimer = 0;
                state.cooldownUntil = 0;
            }
            updateComposer();
        }, 500);
        updateComposer();
    }

    function notice(text, isError = false) {
        const node = state.els.notice;
        node.textContent = text;
        node.classList.toggle("is-error", isError);
        node.hidden = !text;
    }

    /* ---------------------------------------------------------------------
       API
       --------------------------------------------------------------------- */

    async function loadConfig() {
        try {
            const response = await fetch("/api/config", {
                headers: { Accept: "application/json" },
                cache: "no-store",
            });
            if (!response.ok) throw new Error("config unavailable");
            const config = await response.json();
            state.config = { ...DEFAULT_CONFIG, ...config };
        } catch {
            state.config = { ...DEFAULT_CONFIG };
        }

        const max = clamp(Number(state.config.max_message_chars) || 4000, 200, 16000);
        state.config.max_message_chars = max;
        state.els.prompt.maxLength = max;
        state.els.prompt.placeholder = `Message ${state.config.app_name}…`;

        const apiLink = $("#apiLink");
        if (apiLink) apiLink.hidden = !state.config.docs_enabled;

        if (!state.config.ready) {
            notice("Chat is temporarily unavailable. Please check back soon.", true);
            state.els.prompt.placeholder = "Chat is temporarily unavailable";
            updateHeader();
        } else {
            notice("");
        }
        updateComposer();
    }

    async function readHttpError(response) {
        let body = null;
        try {
            body = await response.json();
        } catch {
            /* non-JSON error */
        }
        const retryAfter = Number(response.headers.get("Retry-After")) || 0;
        return new ApiError(
            body?.error?.message || `Request failed with status ${response.status}.`,
            {
                status: response.status,
                code: body?.error?.code || "",
                requestId: body?.error?.request_id || response.headers.get("X-Request-ID") || "",
                retryAfter,
            }
        );
    }

    function parseSseEvent(raw) {
        let name = "message";
        const data = [];
        for (const line of raw.split("\n")) {
            if (!line || line.startsWith(":")) continue;
            const colon = line.indexOf(":");
            const field = colon === -1 ? line : line.slice(0, colon);
            let value = colon === -1 ? "" : line.slice(colon + 1);
            if (value.startsWith(" ")) value = value.slice(1);
            if (field === "event") name = value;
            else if (field === "data") data.push(value);
        }
        if (!data.length) return null;
        try {
            return { name, data: JSON.parse(data.join("\n")) };
        } catch {
            return null;
        }
    }

    async function requestCompletion(conversation, userMessage, assistantMessage, refs) {
        const streamState = {
            controller: new AbortController(),
            conversationId: conversation.id,
            messageId: assistantMessage.id,
            startedAt: performance.now(),
            fullText: "",
            stopped: false,
            timedOut: false,
        };
        const renderer = createStreamRenderer(refs.rich);
        state.streaming = streamState;
        state.els.messages.setAttribute("aria-busy", "true");
        updateHeader();
        updateComposer();

        let fullText = "";
        let firstTokenAt = null;
        let model = "";
        let requestId = "";
        let doneData = null;
        let receivedError = null;

        let slowTimer = window.setTimeout(() => {
            if (firstTokenAt === null && refs.thinkingLabel && !refs.thinking.hidden) {
                refs.thinkingLabel.textContent = "Still thinking…";
            }
        }, FIRST_TOKEN_HINT_MS);

        const watchdog = window.setTimeout(() => {
            if (firstTokenAt === null && !streamState.stopped) {
                streamState.timedOut = true;
                streamState.controller.abort();
            }
        }, WATCHDOG_MS);

        const handleEvent = ({ name, data }) => {
            if (!data || typeof data !== "object") return;

            if (name === "meta") {
                if (data.request_id) requestId = String(data.request_id);
                if (data.model) {
                    model = String(data.model);
                    if (refs.badge) {
                        refs.badge.textContent = modeLabel(data.mode || conversation.mode);
                        refs.badge.title = `Model: ${model}`;
                    }
                }
                return;
            }

            if (name === "status") {
                if (refs.thinkingLabel) refs.thinkingLabel.textContent = "Reasoning…";
                return;
            }

            if (name === "delta") {
                const text = typeof data.text === "string" ? data.text : "";
                if (!text) return;
                if (firstTokenAt === null) {
                    firstTokenAt = performance.now();
                    window.clearTimeout(slowTimer);
                    if (refs.thinking) refs.thinking.hidden = true;
                    if (refs.rich) refs.rich.classList.add("is-streaming");
                }
                fullText += text;
                streamState.fullText = fullText;
                scheduleStreamRender(renderer, fullText);
                return;
            }

            if (name === "done") {
                doneData = data;
                if (data.request_id) requestId = String(data.request_id);
                if (data.model) model = String(data.model);
                return;
            }

            if (name === "error") {
                receivedError = {
                    message: String(data.message || "The response was interrupted."),
                    code: String(data.code || ""),
                    requestId: String(data.request_id || ""),
                };
                if (receivedError.requestId) requestId = receivedError.requestId;
            }
        };

        try {
            const response = await fetch("/api/chat", {
                method: "POST",
                headers: {
                    "Content-Type": "application/json",
                    Accept: "text/event-stream",
                },
                body: JSON.stringify({
                    message: userMessage.content,
                    history: buildHistory(conversation, userMessage, assistantMessage.id),
                    mode: conversation.mode,
                }),
                signal: streamState.controller.signal,
            });

            if (!response.ok) throw await readHttpError(response);
            if (!response.body) {
                throw new ApiError("This browser does not support streaming responses.", {
                    status: 0,
                });
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = "";

            while (true) {
                const { value, done } = await reader.read();
                if (done) break;
                buffer += decoder.decode(value, { stream: true });
                buffer = buffer.replace(/\r\n/g, "\n");

                let boundary = buffer.indexOf("\n\n");
                while (boundary !== -1) {
                    const raw = buffer.slice(0, boundary);
                    buffer = buffer.slice(boundary + 2);
                    const parsed = parseSseEvent(raw);
                    if (parsed) handleEvent(parsed);
                    boundary = buffer.indexOf("\n\n");
                }
            }

            buffer += decoder.decode();
            if (buffer.trim()) {
                const parsed = parseSseEvent(buffer);
                if (parsed) handleEvent(parsed);
            }
        } catch (error) {
            const aborted = error.name === "AbortError" || streamState.stopped;
            if (aborted) {
                if (streamState.timedOut) {
                    receivedError = {
                        message: "The response timed out. Please try again.",
                        code: "timeout",
                        requestId,
                    };
                } else {
                    streamState.stopped = true;
                }
            } else {
                const message = error instanceof Error ? error.message : "Connection lost.";
                receivedError = {
                    message,
                    code: error.code || "",
                    requestId: error.requestId || requestId,
                };
                if (error.status === 429) {
                    startCooldown(error.retryAfter || 30);
                }
                if (error.status === 503 && !state.config.ready) {
                    notice("Chat is temporarily unavailable. Please check back soon.", true);
                }
            }
        } finally {
            window.clearTimeout(slowTimer);
            window.clearTimeout(watchdog);
            cancelStreamRender(renderer);

            const elapsed = Math.round(performance.now() - streamState.startedAt);
            assistantMessage.content = fullText.trimEnd();
            assistantMessage.model = model || assistantMessage.model;
            assistantMessage.mode = conversation.mode;
            assistantMessage.ttft_ms = doneData?.ttft_ms
                ?? (firstTokenAt !== null ? Math.round(firstTokenAt - streamState.startedAt) : null);
            assistantMessage.elapsed_ms = doneData?.elapsed_ms ?? elapsed;
            assistantMessage.cached = Boolean(doneData?.cached);
            assistantMessage.requestId = requestId;
            assistantMessage.error = receivedError?.message || null;
            assistantMessage.stopped = Boolean(streamState.stopped && assistantMessage.content);
            if (!assistantMessage.content && !assistantMessage.error && !assistantMessage.stopped) {
                assistantMessage.error = "No response was received. Please try again.";
            }

            conversation.updatedAt = Date.now();
            const wasCurrent = state.streaming === streamState;
            if (wasCurrent) state.streaming = null;

            const wasStoppedEmpty = streamState.stopped && !assistantMessage.content;
            if (wasStoppedEmpty) {
                removeMessage(conversation, assistantMessage.id);
                refs.article.remove();
            } else {
                const isLast = isLastAssistantMessage(conversation, assistantMessage);
                const fresh = createMessageElement(assistantMessage, conversation, { isLast });
                refs.article.replaceWith(fresh.article);
                if (assistantMessage.content) void highlightCode(fresh.rich);
                if (assistantMessage.error && !assistantMessage.content) {
                    toast(assistantMessage.error, { type: "error", duration: 5000 });
                }
            }

            saveStore();
            if (wasCurrent) {
                state.els.messages.setAttribute("aria-busy", "false");
                if (wasStoppedEmpty) toast("Generation stopped");
                renderSidebar();
                updateHeader();
                updateComposer();
                maybeScroll();
                if (isFinePointer && !wasStoppedEmpty) state.els.prompt.focus();
            }
        }
    }

    function stopStreaming(detach = false) {
        if (!state.streaming) return;
        state.streaming.stopped = true;
        state.streaming.controller.abort();
        if (detach) state.streaming = null;
    }

    async function sendMessage() {
        if (state.streaming || !state.config.ready) return;
        if (Date.now() < state.cooldownUntil) {
            toast("Please wait a moment before sending again.", { type: "error" });
            return;
        }

        const prompt = state.els.prompt;
        let text = prompt.value.trim();
        if (!text) return;
        if (text.length > state.config.max_message_chars) {
            text = text.slice(0, state.config.max_message_chars);
        }

        const conversation = ensureConversation();
        const now = Date.now();
        const userMessage = {
            id: uid(),
            role: "user",
            content: text,
            ts: now,
            mode: conversation.mode,
        };
        conversation.messages.push(userMessage);
        if (conversation.messages.filter((message) => message.role === "user").length === 1) {
            conversation.title = titleFromText(text);
            state.els.chatTitle.textContent = conversation.title;
        }
        conversation.updatedAt = now;

        prompt.value = "";
        resizePrompt();
        updateComposer();

        hideWelcome();
        appendMessage(userMessage, conversation);
        maybeScroll(true);

        const assistantMessage = {
            id: uid(),
            role: "assistant",
            content: "",
            ts: now + 1,
            mode: conversation.mode,
        };
        conversation.messages.push(assistantMessage);
        const refs = appendMessage(assistantMessage, conversation, { isLast: true });

        saveStore();
        renderSidebar();
        updateHeader();
        await requestCompletion(conversation, userMessage, assistantMessage, refs);
    }

    async function regenerate(messageId) {
        if (state.streaming) return;
        if (Date.now() < state.cooldownUntil) {
            toast("Please wait a moment before retrying.", { type: "error" });
            return;
        }

        const conversation = getActiveConversation();
        if (!conversation) return;
        const index = conversation.messages.findIndex((message) => message.id === messageId);
        if (index < 0) return;
        const target = conversation.messages[index];
        if (target.role !== "assistant" || index !== conversation.messages.length - 1) return;

        let userIndex = index - 1;
        while (userIndex >= 0 && conversation.messages[userIndex].role !== "user") userIndex -= 1;
        if (userIndex < 0) return;
        const userMessage = conversation.messages[userIndex];

        conversation.messages.splice(index, 1);
        renderActiveConversation();

        const assistantMessage = {
            id: uid(),
            role: "assistant",
            content: "",
            ts: Date.now(),
            mode: conversation.mode,
        };
        conversation.messages.push(assistantMessage);
        const refs = appendMessage(assistantMessage, conversation, { isLast: true });
        maybeScroll(true);

        saveStore();
        renderSidebar();
        updateHeader();
        await requestCompletion(conversation, userMessage, assistantMessage, refs);
    }

    /* ---------------------------------------------------------------------
       Sidebar / mobile
       --------------------------------------------------------------------- */

    function openSidebar() {
        document.body.classList.add("sidebar-open");
        state.els.sidebarBackdrop.hidden = false;
        state.els.openSidebar.setAttribute("aria-expanded", "true");
        state.els.closeSidebar.focus();
    }

    function closeSidebar(restoreFocus = true) {
        document.body.classList.remove("sidebar-open");
        state.els.sidebarBackdrop.hidden = true;
        state.els.openSidebar.setAttribute("aria-expanded", "false");
        if (restoreFocus && isFinePointer === false) state.els.openSidebar.focus();
    }

    /* ---------------------------------------------------------------------
       Boot
       --------------------------------------------------------------------- */

    function bindEvents() {
        state.els.composer.addEventListener("submit", (event) => {
            event.preventDefault();
            void sendMessage();
        });

        state.els.prompt.addEventListener("input", () => {
            resizePrompt();
            updateCharCount();
            updateComposer();
        });

        state.els.prompt.addEventListener("keydown", (event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
                event.preventDefault();
                void sendMessage();
            }
        });

        state.els.stopBtn.addEventListener("click", () => stopStreaming());
        state.els.newChat.addEventListener("click", newChat);
        state.els.newChatTop.addEventListener("click", newChat);
        state.els.themeToggle.addEventListener("click", toggleTheme);

        state.els.modeControl.addEventListener("click", (event) => {
            const button = event.target.closest("[data-mode]");
            if (button) setMode(button.dataset.mode);
        });

        state.els.searchChats.addEventListener("input", () => {
            state.filter = state.els.searchChats.value;
            renderSidebar();
        });

        state.els.openSidebar.addEventListener("click", openSidebar);
        state.els.closeSidebar.addEventListener("click", () => closeSidebar());
        state.els.sidebarBackdrop.addEventListener("click", () => closeSidebar());

        state.els.messages.addEventListener("scroll", () => {
            const near = isNearBottom();
            state.els.scrollBottom.hidden = near || state.els.messages.scrollHeight <= state.els.messages.clientHeight + 20;
        }, { passive: true });

        state.els.scrollBottom.addEventListener("click", () => maybeScroll(true));

        state.els.messages.addEventListener("click", (event) => {
            const suggestion = event.target.closest(".suggestion");
            if (!suggestion) return;
            state.els.prompt.value = suggestion.dataset.prompt || "";
            state.els.prompt.focus();
            resizePrompt();
            updateComposer();
        });

        document.addEventListener("keydown", (event) => {
            const modifier = event.ctrlKey || event.metaKey;
            if (modifier && event.key.toLowerCase() === "k") {
                event.preventDefault();
                newChat();
                return;
            }
            if (event.key === "Escape") {
                if (document.body.classList.contains("sidebar-open")) {
                    closeSidebar();
                } else if (state.streaming) {
                    stopStreaming();
                }
            }
        });

        window.addEventListener("storage", (event) => {
            if (event.key !== STORAGE_KEY || state.streaming) return;
            loadStore();
            renderSidebar();
            renderActiveConversation();
        });

        window.addEventListener("beforeunload", () => {
            if (state.streaming) {
                const conversation = state.conversations.find(
                    (item) => item.id === state.streaming.conversationId
                );
                const message = conversation?.messages.find(
                    (item) => item.id === state.streaming.messageId
                );
                if (message && state.streaming.fullText) {
                    message.content = state.streaming.fullText;
                    message.error = "Response was interrupted.";
                }
                saveStore();
            }
        });

        document.addEventListener("visibilitychange", () => {
            if (document.visibilityState !== "hidden" || !state.streaming) return;
            const conversation = state.conversations.find(
                (item) => item.id === state.streaming.conversationId
            );
            const message = conversation?.messages.find(
                (item) => item.id === state.streaming.messageId
            );
            if (message && state.streaming.fullText) {
                message.content = state.streaming.fullText;
            }
            saveStore();
        });

        prefersDark.addEventListener("change", () => {
            if (state.theme === "auto") applyTheme();
        });

        const mobileQuery = window.matchMedia("(max-width: 980px)");
        mobileQuery.addEventListener("change", (event) => {
            if (!event.matches) closeSidebar(false);
        });

        window.addEventListener("online", () => toast("Back online"));
        window.addEventListener("offline", () => toast("You're offline. Messages won't send.", { type: "error" }));
    }

    function registerServiceWorker() {
        const host = window.location.hostname;
        const isLocal = host === "localhost" || host === "127.0.0.1" || host === "[::1]";
        const forcePwa = new URLSearchParams(window.location.search).has("pwa");
        if (!("serviceWorker" in navigator)) return;
        if (window.location.protocol !== "https:" && !(isLocal && forcePwa)) return;
        window.addEventListener("load", () => {
            navigator.serviceWorker.register("/sw.js").catch(() => {
                /* PWA support is optional. */
            });
        });
    }

    function applyPlatformHints() {
        const isMac = /Mac|iPhone|iPad|iPod/.test(navigator.platform || "");
        if (!isMac) return;
        $$(".shortcut").forEach((node) => {
            node.textContent = "⌘K";
        });
        const welcomeKbd = $(".welcome-note kbd");
        if (welcomeKbd) welcomeKbd.textContent = "⌘";
    }

    function cacheElements() {
        state.els = {
            messages: $("#messages"),
            prompt: $("#prompt"),
            composer: $("#composer"),
            sendBtn: $("#sendBtn"),
            stopBtn: $("#stopBtn"),
            charCount: $("#charCount"),
            modeHint: $("#modeHint"),
            chatTitle: $("#chatTitle"),
            chatSubtitle: $("#chatSubtitle"),
            conversationList: $("#conversationList"),
            searchWrap: $("#searchWrap"),
            searchChats: $("#searchChats"),
            newChat: $("#newChat"),
            newChatTop: $("#newChatTop"),
            themeToggle: $("#themeToggle"),
            themeLabel: $("#themeLabel"),
            modeControl: $(".mode-control"),
            openSidebar: $("#openSidebar"),
            closeSidebar: $("#closeSidebar"),
            sidebarBackdrop: $("#sidebarBackdrop"),
            scrollBottom: $("#scrollBottom"),
            toasts: $("#toasts"),
            notice: $("#notice"),
        };
    }

    function init() {
        cacheElements();

        const storedTheme = safeStorageGet(THEME_KEY);
        if (storedTheme === "light" || storedTheme === "dark") state.theme = storedTheme;
        const storedMode = safeStorageGet(MODE_KEY);
        if (storedMode === "smart" || storedMode === "fast") state.mode = storedMode;

        loadStore();
        applyTheme();
        bindEvents();
        applyPlatformHints();
        renderSidebar();
        renderActiveConversation();
        updateHeader();
        updateModeUI();
        resizePrompt();
        updateComposer();
        registerServiceWorker();
        void loadConfig();

        if (isFinePointer) state.els.prompt.focus();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init, { once: true });
    } else {
        init();
    }
})();
