import { isValidElement, type ReactElement, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "@/api/errors";
import { createUiI18n } from "@/i18n";
import OAuthConsent from "./OAuthConsent";

const harness = vi.hoisted(() => ({
  states: [] as unknown[], refs: [] as Array<{ current: unknown }>, stateCursor: 0, refCursor: 0,
  effect: null as (() => void | (() => void)) | null, cleanup: null as (() => void) | null,
  deps: [] as unknown[], nextDeps: [] as unknown[], authorize: vi.fn(), assign: vi.fn(), logout: vi.fn(),
  user: { id: "customer-user", role: "customer", customerId: "customer", identityVersion: 1, email: "customer@example.test" } as Record<string, unknown> | null,
  sessionKey: 1, loading: false, search: "", hash: "", token: "synthetic-jwt", translate: (key: string) => key,
}));
vi.mock("react", async (original) => ({
  ...await original<typeof import("react")>(),
  useState: (initial: unknown) => {
    const index = harness.stateCursor++;
    if (!(index in harness.states)) harness.states[index] = initial;
    return [harness.states[index], (value: unknown) => { harness.states[index] = value; }];
  },
  useRef: (initial: unknown) => {
    const index = harness.refCursor++;
    if (!(index in harness.refs)) harness.refs[index] = { current: initial };
    return harness.refs[index];
  },
  useEffect: (effect: () => void | (() => void), deps: unknown[]) => { harness.effect = effect; harness.nextDeps = deps; },
}));
vi.mock("react-router-dom", () => ({ useLocation: () => ({ search: harness.search, hash: harness.hash }) }));
vi.mock("react-i18next", async (original) => ({
  ...await original<typeof import("react-i18next")>(),
  useTranslation: () => ({ t: (key: string) => harness.translate(key) }),
}));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => harness }));
vi.mock("@/api/oauthClient", async (original) => ({ ...await original<typeof import("@/api/oauthClient")>(), authorizeOAuth: harness.authorize }));

const request = {
  client_id: "registered-client", redirect_uri: "https://client.example/callback?tenant=demo", response_type: "code",
  scope: "mcp.access", state: "original-state", code_challenge: "a".repeat(43), code_challenge_method: "S256",
};
const search = "?" + new URLSearchParams(request);
const callback = request.redirect_uri + "&code=opaque-code&state=original-state";
type Element = ReactElement<Record<string, unknown>>;
function elements(node: ReactNode): Element[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement<Record<string, unknown>>(node)) return [];
  return [node, ...elements(node.props.children as ReactNode)];
}
function text(node: ReactNode): string {
  if (Array.isArray(node)) return node.map(text).join("");
  if (isValidElement<Record<string, unknown>>(node)) return text(node.props.children as ReactNode);
  return typeof node === "string" ? node : "";
}
function render(): Element {
  harness.stateCursor = 0; harness.refCursor = 0;
  const tree = OAuthConsent();
  if (harness.nextDeps.some((entry, index) => entry !== harness.deps[index])) {
    harness.cleanup?.();
    harness.cleanup = harness.effect?.() || null;
    harness.deps = harness.nextDeps;
  }
  return tree;
}
function button(label: string): Element {
  const found = elements(render()).find((node) => typeof node.props.onClick === "function" && text(node.props.children as ReactNode) === label);
  if (!found) throw new Error("Missing consent control");
  return found;
}
function click(label: string): void { (button(label).props.onClick as () => void)(); }
async function settle(): Promise<void> { await new Promise<void>((resolve) => setImmediate(resolve)); }

beforeEach(() => {
  vi.clearAllMocks();
  harness.states = []; harness.refs = []; harness.deps = []; harness.cleanup = null;
  harness.user = { id: "customer-user", role: "customer", customerId: "customer", identityVersion: 1, email: "customer@example.test" };
  harness.sessionKey = 1; harness.loading = false; harness.search = search; harness.hash = "";
  harness.token = "synthetic-jwt"; harness.translate = (key) => key;
  harness.authorize.mockResolvedValue(callback);
  vi.stubGlobal("localStorage", { getItem: () => harness.token });
  vi.stubGlobal("window", { location: { assign: harness.assign } });
});
afterEach(() => { harness.cleanup?.(); vi.unstubAllGlobals(); });

describe("explicit single-flight consent", () => {
  it("never dispatches on mount and approves only on an explicit click", async () => {
    const html = text(render());
    expect(html).toContain("registered-client");
    expect(html).toContain(request.redirect_uri);
    expect(html).not.toContain("original-state");
    expect(html).not.toContain(request.code_challenge);
    expect(harness.authorize).not.toHaveBeenCalled();
    click("OAuth approve");
    await settle();
    expect(harness.authorize).toHaveBeenCalledWith(request, true, expect.any(AbortSignal));
    expect(harness.assign).toHaveBeenCalledWith(callback);
    expect(button("OAuth approve").props.disabled).toBe(true);
    expect(button("OAuth deny").props.disabled).toBe(true);
  });
  it("denies explicitly and blocks competing decisions before rerender", async () => {
    let finish!: (value: string) => void;
    harness.authorize.mockImplementation(() => new Promise<string>((resolve) => { finish = resolve; }));
    const approve = button("OAuth approve").props.onClick as () => void;
    const deny = button("OAuth deny").props.onClick as () => void;
    deny(); approve(); deny();
    expect(harness.authorize).toHaveBeenCalledOnce();
    expect(harness.authorize.mock.calls[0][1]).toBe(false);
    const denial = request.redirect_uri + "&error=access_denied&state=original-state";
    finish(denial); await settle();
    expect(harness.assign).toHaveBeenCalledWith(denial);
  });
  it.each([new Error("network failure"), new ApiError("SERVICE_UNAVAILABLE"), new ApiError("ACCESS_DENIED")])("locks failed/uncertain decisions without retry", async (failure) => {
    harness.authorize.mockRejectedValue(failure);
    click("OAuth approve"); await settle();
    expect(elements(render()).some((node) => node.props.role === "alert")).toBe(true);
    expect(text(render())).not.toContain("network failure");
    expect(button("OAuth approve").props.disabled).toBe(true);
    expect(button("OAuth deny").props.disabled).toBe(true);
    click("OAuth deny"); click("OAuth approve"); await settle();
    expect(harness.authorize).toHaveBeenCalledOnce();
    expect(harness.assign).not.toHaveBeenCalled();
  });
  it("fails closed on expired authentication", async () => {
    harness.authorize.mockRejectedValue(new ApiError("AUTH_REQUIRED"));
    click("OAuth approve"); await settle();
    expect(harness.logout).toHaveBeenCalledOnce();
    expect(harness.assign).not.toHaveBeenCalled();
  });
  it.each(["invalid", "fragment", "staff", "anonymous", "loading"])("cannot dispatch for %s", (state) => {
    if (state === "invalid") harness.search += "&state=duplicate";
    if (state === "fragment") harness.hash = "#fragment";
    if (state === "staff") harness.user!.role = "operator";
    if (state === "anonymous") harness.user = null;
    if (state === "loading") harness.loading = true;
    const controls = elements(render()).filter((node) => typeof node.props.onClick === "function");
    for (const control of controls) {
      if (text(control.props.children as ReactNode) !== "Sign out") (control.props.onClick as () => void)();
    }
    expect(harness.authorize).not.toHaveBeenCalled();
    expect(harness.assign).not.toHaveBeenCalled();
  });
});

describe("stale consent isolation", () => {
  it.each(["unmount", "logout", "session", "subject", "customer", "version", "role", "loading", "query", "token"])("ignores pending results after %s", async (change) => {
    let finish!: (value: string) => void;
    harness.authorize.mockImplementation(() => new Promise<string>((resolve) => { finish = resolve; }));
    click("OAuth approve");
    const signal = harness.authorize.mock.calls[0][2] as AbortSignal;
    if (change === "unmount") harness.cleanup?.();
    else {
      if (change === "logout") harness.user = null;
      if (change === "session") harness.sessionKey++;
      if (change === "subject") harness.user!.id = "other-user";
      if (change === "customer") harness.user!.customerId = "other-customer";
      if (change === "version") harness.user!.identityVersion = 2;
      if (change === "role") harness.user!.role = "operator";
      if (change === "loading") harness.loading = true;
      if (change === "query") harness.search = search.replace("original-state", "changed-state");
      if (change === "token") harness.token = "other-jwt";
      render();
    }
    if (change !== "token") expect(signal.aborted).toBe(true);
    finish(callback); await settle();
    expect(harness.assign).not.toHaveBeenCalled();
    expect(harness.logout).not.toHaveBeenCalled();
  });
  it.each(["unmount", "session"])("rejects a stale pre-submit handler after %s", (change) => {
    const handler = button("OAuth approve").props.onClick as () => void;
    if (change === "unmount") harness.cleanup?.();
    else { harness.sessionKey++; render(); }
    handler();
    expect(harness.authorize).not.toHaveBeenCalled();
  });
  it("aborts immediately on explicit sign out before a provider result", async () => {
    let finish!: (value: string) => void;
    harness.authorize.mockImplementation(() => new Promise<string>((resolve) => { finish = resolve; }));
    click("OAuth approve"); click("Sign out");
    expect(harness.authorize.mock.calls[0][2].aborted).toBe(true);
    expect(harness.logout).toHaveBeenCalledOnce();
    finish(callback); await settle();
    expect(harness.assign).not.toHaveBeenCalled();
  });
  it("ignores a late expired-auth failure after identity switching", async () => {
    let fail!: (error: unknown) => void;
    harness.authorize.mockImplementation(() => new Promise((_, reject) => { fail = reject; }));
    click("OAuth deny");
    harness.sessionKey++; render();
    fail(new ApiError("AUTH_REQUIRED")); await settle();
    expect(harness.logout).not.toHaveBeenCalled();
  });
});

describe("consent locale catalogs", () => {
  it.each(["en", "es", "pt"])("localizes consent in %s without translating protocol", (locale) => {
    const i18n = createUiI18n(locale);
    harness.translate = (key) => String(i18n.t(key, { lng: locale }));
    expect(button(harness.translate("OAuth approve"))).toBeDefined();
    expect(button(harness.translate("OAuth deny"))).toBeDefined();
    expect(text(render())).toContain("mcp.access");
    for (const key of ["OAuth prototype notice", "OAuth invalid request", "OAuth consent uncertain"]) {
      expect(harness.translate(key)).not.toBe(key);
    }
  });
});
