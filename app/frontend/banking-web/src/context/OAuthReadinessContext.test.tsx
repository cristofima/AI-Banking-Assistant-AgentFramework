import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { OAuthReadinessProvider } from "./OAuthReadinessContext";
import { ApiError } from "@/api/errors";
const h = vi.hoisted(() => ({
  slots: [] as unknown[], cursor: 0, queued: [] as Array<() => void>,
  effects: [] as Array<{ deps: unknown[]; cleanup?: () => void }>,
  user: { id: "u", role: "customer", customerId: "c", identityVersion: 1 } as Record<string, unknown> | null,
  sessionKey: 1, loading: false, token: "jwt", status: vi.fn(), start: vi.fn(), logout: vi.fn(), assign: vi.fn(),
  focus: null as null | (() => void),
}));
vi.mock("react", async original => ({
  ...await original<typeof import("react")>(),
  useState: (initial: unknown) => { const i = h.cursor++; if (!(i in h.slots)) h.slots[i] = initial; return [h.slots[i], (next: unknown) => { h.slots[i] = next; }]; },
  useRef: (current: unknown) => h.slots[h.cursor++] ??= { current },
  useCallback: (callback: unknown, deps: unknown[]) => {
    const i = h.cursor++; const old = h.slots[i] as { callback: unknown; deps: unknown[] } | undefined;
    if (!old || deps.some((dep, n) => dep !== old.deps[n])) h.slots[i] = { callback, deps };
    return (h.slots[i] as { callback: unknown }).callback;
  },
  useEffect: (effect: () => void | (() => void), deps: unknown[]) => {
    const i = h.cursor++; const old = h.effects[i];
    if (!old || deps.some((dep, n) => dep !== old.deps[n])) h.queued.push(() => { old?.cleanup?.(); h.effects[i] = { deps, cleanup: effect() || undefined }; });
  },
}));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => h }));
vi.mock("@/api/oauthClient", () => ({ getOAuthStatus: h.status, startOAuth: h.start }));
function render() { h.cursor = 0; const result = OAuthReadinessProvider({ children: null }).props.value; h.queued.splice(0).forEach(effect => effect()); return result; }
async function settle() { await new Promise<void>(resolve => setImmediate(resolve)); }
beforeEach(() => {
  vi.clearAllMocks(); vi.useFakeTimers({ toFake: ["setInterval", "clearInterval"] });
  h.slots = []; h.effects = []; h.queued = []; h.focus = null;
  h.user = { id: "u", role: "customer", customerId: "c", identityVersion: 1 }; h.sessionKey = 1; h.loading = false; h.token = "jwt";
  h.status.mockResolvedValue({ required: false, ready: false }); h.start.mockResolvedValue("/oauth/consent?valid");
  vi.stubGlobal("window", { location: { assign: h.assign }, addEventListener: (_name: string, callback: () => void) => { h.focus = callback; }, removeEventListener: () => { h.focus = null; } });
  vi.stubGlobal("localStorage", { getItem: () => h.token });
});
afterEach(() => { h.effects.forEach(effect => effect?.cleanup?.()); vi.useRealTimers(); vi.unstubAllGlobals(); });
describe("shared session-scoped readiness", () => {
  it("preserves HMAC behavior and checks on focus/timer without grant renewal", async () => {
    expect(render().status).toBeNull(); await settle();
    expect(render().status).toEqual({ required: false, ready: false });
    expect(await render().verify()).toBe(true);
    h.focus?.(); await settle(); vi.advanceTimersByTime(30_000); await settle();
    expect(h.status).toHaveBeenCalledTimes(4); expect(h.start).not.toHaveBeenCalled();
  });
  it("removes formerly ready status on 503 and allows only explicit read-only recheck", async () => {
    h.status.mockResolvedValue({ required: true, ready: true }); render(); await settle();
    expect(render().status?.ready).toBe(true);
    h.status.mockRejectedValue(new ApiError("SERVICE_UNAVAILABLE"));
    expect(await render().verify()).toBe(false);
    expect(render()).toMatchObject({ status: null, error: true, busy: false });
    expect(h.start).not.toHaveBeenCalled();
  });
  it("starts once explicitly and never retries an uncertain start", async () => {
    h.status.mockResolvedValue({ required: true, ready: false }); render(); await settle();
    h.start.mockRejectedValue(new Error("uncertain"));
    const value = render(); await Promise.all([value.authorize(), value.authorize()]);
    await render().authorize(); expect(h.start).toHaveBeenCalledOnce(); expect(h.assign).not.toHaveBeenCalled();
    // Status reconciliation remains available even though start must not replay.
    expect(await render().verify()).toBe(false); expect(h.status).toHaveBeenCalledTimes(2);
  });
  it("keeps uncertain start locked across same-session profile rerenders", async () => {
    h.status.mockResolvedValue({ required: true, ready: false }); render(); await settle();
    h.start.mockRejectedValue(new Error("uncertain")); await render().authorize();
    h.user = { ...h.user }; render(); await settle();
    expect(render().startLocked).toBe(true); await render().authorize();
    expect(h.start).toHaveBeenCalledOnce();
  });
  it("navigates only after explicit start for the current customer", async () => {
    h.status.mockResolvedValue({ required: true, ready: false }); render(); await settle();
    expect(h.start).not.toHaveBeenCalled(); await render().authorize();
    expect(h.assign).toHaveBeenCalledExactlyOnceWith("/oauth/consent?valid");
  });
  it.each(["session", "subject", "version", "customer", "role", "unmount"])("aborts status and rejects stale result after %s", async change => {
    let finish!: (value: unknown) => void;
    h.status.mockImplementationOnce(() => new Promise(resolve => { finish = resolve; })); render();
    const signal = h.status.mock.calls[0][0] as AbortSignal;
    if (change === "session") h.sessionKey++;
    if (change === "subject") h.user!.id = "other";
    if (change === "version") h.user!.identityVersion = 2;
    if (change === "customer") h.user!.customerId = "other";
    if (change === "role") h.user!.role = "admin";
    if (change === "unmount") h.effects.forEach(effect => effect?.cleanup?.()); else render();
    finish({ required: true, ready: true }); await settle();
    expect(signal.aborted).toBe(true); expect(render().status?.ready).not.toBe(true);
  });
  it("logs out on expired application identity", async () => {
    h.status.mockRejectedValue(new ApiError("AUTH_REQUIRED")); render(); await settle();
    expect(h.logout).toHaveBeenCalledOnce(); expect(render().status).toBeNull();
  });
  it.each(["anonymous", "staff", "restoring"])("does not dispatch for %s", state => {
    if (state === "anonymous") h.user = null;
    if (state === "staff") h.user!.role = "operator";
    if (state === "restoring") h.loading = true;
    render(); expect(h.status).not.toHaveBeenCalled(); expect(h.start).not.toHaveBeenCalled();
  });
});
