// Calls to api: base address, API token, Idempotency-Key, Problem Details, polling of a final state.
// Serve the client outside Compose:  python -m http.server 8083 --bind 127.0.0.1 --directory client

export const API = localStorage.getItem("api") ?? "http://localhost:8000";
export const FINAL = new Set(["done", "rejected", "failed", "cancelled"]);

export class Problem extends Error {
    constructor(status, body) {
        super(body?.title ?? `HTTP ${status}`);
        this.status = status;
        this.body = body;
    }
}

export const getToken = () => sessionStorage.getItem("token") ?? "dev-token";
export const setToken = (value) => sessionStorage.setItem("token", value);
export const newKey = () => crypto.randomUUID();

export async function call(method, path, {body, idempotencyKey} = {}) {
    const reply = await fetch(API + path, {
        method,
        headers: {
            Authorization: `Bearer ${getToken()}`,
            ...(body !== undefined ? {"Content-Type": "application/json"} : {}),
            ...(idempotencyKey ? {"Idempotency-Key": idempotencyKey} : {}),
        },
        body: body !== undefined ? JSON.stringify(body) : undefined,
    });

    const type = reply.headers.get("content-type") ?? "";
    const data = type.includes("json") ? await reply.json() : await reply.text();
    if (!reply.ok) {
        throw new Problem(reply.status, typeof data === "object" ? data : {title: data});
    }

    return {status: reply.status, headers: reply.headers, data};
}

export async function waitFinal(path, {field = "state", everyMs = 500, timeoutMs = 120_000, onStep} = {}) {
    const deadline = Date.now() + timeoutMs;

    while (Date.now() < deadline) {
        const reply = await call("GET", path);
        onStep?.(reply.data);
        if (FINAL.has(reply.data?.[field])) {
            return reply.data;
        }

        const retry = Number(reply.headers.get("retry-after"));
        await new Promise((resolve) => setTimeout(resolve, retry > 0 ? retry * 1000 : everyMs));
    }

    throw new Error(`no final ${field} within ${timeoutMs / 1000} s`);
}
