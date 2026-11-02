/* Jarvis AI service worker — precaches the app shell, never the API. */

const VERSION = "__ASSET_VERSION__";
const CACHE_NAME = `jarvis-shell-${VERSION}`;
const SHELL = [
    "/",
    `/static/styles.css?v=${VERSION}`,
    `/static/app.js?v=${VERSION}`,
    `/static/vendor/marked.min.js?v=${VERSION}`,
    `/static/vendor/purify.min.js?v=${VERSION}`,
    `/static/favicon.svg?v=${VERSION}`,
    `/static/manifest.webmanifest?v=${VERSION}`,
];

self.addEventListener("install", (event) => {
    event.waitUntil((async () => {
        const cache = await caches.open(CACHE_NAME);
        await Promise.allSettled(
            SHELL.map((url) => cache.add(new Request(url, { cache: "reload" })))
        );
        await self.skipWaiting();
    })());
});

self.addEventListener("activate", (event) => {
    event.waitUntil((async () => {
        const keys = await caches.keys();
        await Promise.all(
            keys
                .filter((key) => key.startsWith("jarvis-shell-") && key !== CACHE_NAME)
                .map((key) => caches.delete(key))
        );
        await self.clients.claim();
    })());
});

self.addEventListener("fetch", (event) => {
    const request = event.request;
    if (request.method !== "GET") return;

    const url = new URL(request.url);
    if (url.origin !== self.location.origin) return;
    if (
        url.pathname.startsWith("/api/") ||
        url.pathname === "/sw.js" ||
        url.pathname.startsWith("/docs") ||
        url.pathname === "/openapi.json"
    ) {
        return;
    }

    if (request.mode === "navigate") {
        event.respondWith((async () => {
            try {
                const response = await fetch(request);
                if (response && response.ok) {
                    const cache = await caches.open(CACHE_NAME);
                    cache.put("/", response.clone());
                }
                return response;
            } catch {
                const cached = (await caches.match("/")) || (await caches.match(request));
                return cached || new Response("You are offline.", {
                    status: 503,
                    headers: { "Content-Type": "text/plain; charset=utf-8" },
                });
            }
        })());
        return;
    }

    if (url.pathname.startsWith("/static/")) {
        event.respondWith((async () => {
            const cached = await caches.match(request);
            if (cached) return cached;
            const response = await fetch(request);
            if (response && response.ok) {
                const cache = await caches.open(CACHE_NAME);
                cache.put(request, response.clone());
            }
            return response;
        })());
    }
});
