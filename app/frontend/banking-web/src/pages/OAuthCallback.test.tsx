import { isValidElement, type ReactElement, type ReactNode } from "react";
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/api/errors";
import OAuthCallback from "./OAuthCallback";
import { Button } from "@/components/ui/button";
import en from "@/locales/en.json";
import es from "@/locales/es.json";
import pt from "@/locales/pt.json";

const h = vi.hoisted(() => ({
  slots: [] as unknown[], cursor: 0, queued: [] as Array<() => void>,
  effects: [] as Array<{ deps: unknown[]; cleanup?: () => void }>,
  user: { id: "customer", role: "customer", customerId: "c", identityVersion: 1 } as Record<string, unknown> | null,
  loading: false, sessionKey: 1, search: "?state=s&code=c", hash: "", token: "jwt",
  complete: vi.fn(), logout: vi.fn(), replace: vi.fn(),
}));
vi.mock("react", async original => ({
  ...await original<typeof import("react")>(),
  useState: (initial: unknown) => {
    const i = h.cursor++;
    if (!(i in h.slots)) h.slots[i] = initial;
    return [h.slots[i], (next: unknown) => { h.slots[i] = next; }];
  },
  useRef: (current: unknown) => h.slots[h.cursor++] ??= { current },
  useLayoutEffect: (effect: () => void) => { effect(); },
  useEffect: (effect: () => void | (() => void), deps: unknown[]) => {
    const i = h.cursor++; const previous = h.effects[i];
    if (!previous || deps.some((dep, n) => dep !== previous.deps[n])) h.queued.push(() => {
      previous?.cleanup?.(); h.effects[i] = { deps, cleanup: effect() || undefined };
    });
  },
}));
vi.mock("react-router-dom", () => ({ useLocation: () => h, Link: "a", Navigate: "navigate" }));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => h }));
vi.mock("@/api/oauthClient", async original => ({ ...await original<typeof import("@/api/oauthClient")>(), completeOAuth: h.complete }));
type Element = ReactElement<Record<string, unknown>>;
function nodes(node: ReactNode): Element[] {
  if (Array.isArray(node)) return node.flatMap(nodes);
  if (!isValidElement<Record<string, unknown>>(node)) return [];
  return [node, ...nodes(node.props.children as ReactNode)];
}
function render(): Element { h.cursor = 0; const result = OAuthCallback(); h.queued.splice(0).forEach(effect => effect()); return result; }
function click(): void { nodes(render()).find(node => typeof node.props.onClick === "function")?.props.onClick?.(); }
async function settle(): Promise<void> { await new Promise<void>(resolve => setImmediate(resolve)); }
beforeEach(() => {
  vi.clearAllMocks(); h.slots = []; h.effects = []; h.queued = [];
  h.user = { id: "customer", role: "customer", customerId: "c", identityVersion: 1 };
  h.loading = false; h.sessionKey = 1; h.search = "?state=s&code=c"; h.hash = ""; h.token = "jwt";
  h.complete.mockResolvedValue(undefined);
  vi.stubGlobal("window", { history: { replaceState: h.replace } });
  vi.stubGlobal("localStorage", { getItem: () => h.token, setItem: vi.fn() });
});
afterEach(() => { h.effects.forEach(effect => effect?.cleanup?.()); vi.unstubAllGlobals(); });

describe("callback one-shot exchange", () => {
  it("spaces actions and styles return navigation before and after completion", async () => {
    const actions = nodes(render()).find(node => node.props.className === "flex flex-wrap gap-3")!;
    const buttons = nodes(actions).filter(node => node.type === Button);
    expect(buttons).toHaveLength(2);
    expect(buttons[1].props).toMatchObject({ asChild: true, variant: "outline" });
    expect(nodes(buttons[1]).find(node => node.props.to === "/support")?.props.children).toBe("OAuth return chat");
    click(); await settle();
    const completed = nodes(render()).filter(node => node.type === Button);
    expect(completed).toHaveLength(1);
    expect(completed[0].props).toMatchObject({ asChild: true, variant: "default" });
  });
  it.each([en, es, pt])("does not disclose token lifetime in readiness copy", catalog => {
    for (const key of ["OAuth callback complete", "OAuth chat ready"] as const) {
      expect(catalog[key]).not.toMatch(/\d|minutes|minutos|hours|horas/i);
    }
  });
  it("scrubs URL before dispatch, requires explicit finish and exchanges once", async () => {
    render();
    expect(h.replace).toHaveBeenCalledWith(null, "", "/oauth/callback");
    expect(h.complete).not.toHaveBeenCalled();
    const button = nodes(render()).find(node => typeof node.props.onClick === "function")!;
    const dispatch = button.props.onClick as () => void;
    dispatch(); dispatch(); await settle();
    expect(h.complete).toHaveBeenCalledExactlyOnceWith({ state: "s", code: "c" }, expect.any(AbortSignal));
    expect(nodes(render()).some(node => node.props.children === "OAuth callback complete")).toBe(true);
    expect(localStorage.setItem).not.toHaveBeenCalled();
    expect(JSON.stringify(render())).not.toContain("state=s");
  });
  it.each([new Error("private failure"), new ApiError("ACCESS_DENIED"), new ApiError("SERVICE_UNAVAILABLE")])("locks failures without replay", async failure => {
    h.complete.mockRejectedValue(failure);
    click(); await settle(); click(); await settle();
    expect(h.complete).toHaveBeenCalledOnce();
    expect(nodes(render()).some(node => node.props.children === "OAuth callback uncertain")).toBe(true);
    expect(JSON.stringify(render())).not.toContain("private failure");
  });
  it("expires the session safely", async () => {
    h.complete.mockRejectedValue(new ApiError("AUTH_REQUIRED"));
    click(); await settle(); expect(h.logout).toHaveBeenCalledOnce();
  });
  it.each(["?state=s&error=access_denied", "?state=s&code=c&code=d", "?code=c", "?state=s&access_token=t", "?state=s&code=%ff"]) ("does not exchange invalid/denied query %s", search => {
    h.search = search; click(); expect(h.complete).not.toHaveBeenCalled();
  });
  it.each(["staff", "anonymous", "loading", "fragment"]) ("isolates %s", state => {
    if (state === "staff") h.user!.role = "operator";
    if (state === "anonymous") h.user = null;
    if (state === "loading") h.loading = true;
    if (state === "fragment") h.hash = "#bad";
    click(); expect(h.complete).not.toHaveBeenCalled();
    expect(h.replace).toHaveBeenCalledWith(null, "", "/oauth/callback");
  });
  it.each(["session", "subject", "version", "customer", "role", "token", "unmount"]) ("ignores pending completion after %s", async change => {
    let finish!: () => void;
    h.complete.mockImplementation(() => new Promise<void>(resolve => { finish = resolve; }));
    click();
    const signal = h.complete.mock.calls[0][1] as AbortSignal;
    if (change === "session") h.sessionKey++;
    if (change === "subject") h.user!.id = "other";
    if (change === "version") h.user!.identityVersion = 2;
    if (change === "customer") h.user!.customerId = "other";
    if (change === "role") h.user!.role = "operator";
    if (change === "token") h.token = "other";
    if (change === "unmount") h.effects.forEach(effect => effect?.cleanup?.()); else render();
    finish(); await settle();
    if (change !== "token") expect(signal.aborted).toBe(true);
    expect(nodes(render()).some(node => node.props.children === "OAuth callback complete")).toBe(false);
    expect(h.complete).toHaveBeenCalledOnce();
  });
});
