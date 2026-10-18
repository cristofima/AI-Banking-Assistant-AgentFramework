import { isValidElement, type ReactElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DisputeConsent } from "./DisputeConsent";
import { ApiError } from "@/api/errors";

const h = vi.hoisted(() => ({
  states: [] as unknown[], refs: [] as Array<{ current: unknown }>, cursor: 0,
  effects: [] as Array<{ deps: unknown[]; cleanup?: () => void }>,
  queued: [] as Array<() => void>, get: vi.fn(), respond: vi.fn(), logout: vi.fn(),
  locked: false, sessionKey: 1, user: { id: "customer", identityVersion: 1 } as { id: string; identityVersion: number } | null,
}));
vi.mock("react", async (original) => ({
  ...await original<typeof import("react")>(),
  useState: (initial: unknown) => {
    const index = h.cursor++;
    if (!(index in h.states)) h.states[index] = initial;
    return [h.states[index], (value: unknown) => { h.states[index] = value; }];
  },
  useRef: (initial: unknown) => h.refs[h.cursor++] ??= { current: initial },
  useEffect: (effect: () => void | (() => void), deps: unknown[]) => {
    const index = h.cursor++;
    const previous = h.effects[index];
    if (!previous || deps.some((dep, i) => dep !== previous.deps[i])) {
      h.queued.push(() => {
        previous?.cleanup?.();
        const cleanup = effect();
        h.effects[index] = { deps, cleanup: cleanup || undefined };
      });
    }
  },
}));
vi.mock("react-router-dom", () => ({ Link: "a" }));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock("@/context/AuthContext", () => ({ useAuth: () => ({ user: h.user, sessionKey: h.sessionKey, logout: h.logout }) }));
vi.mock("@/api/disputeClient", () => ({ getSupportCase: h.get, respondToSupportCaseApproval: h.respond }));
const oauth = vi.hoisted(() => ({ value: null as null | { status: { required: boolean; ready: boolean } | null; busy: boolean } }));
vi.mock("@/context/OAuthReadinessContext", () => ({ useOAuthReadiness: () => oauth.value }));
vi.mock("../../ResponsesChatProvider", () => ({ useChat: () => ({ activeThreadId: "thread-1", isThreadLocked: () => h.locked }) }));

type Element = ReactElement<Record<string, unknown>>;
function elements(node: ReactNode): Element[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement<Record<string, unknown>>(node)) return [];
  return [node, ...elements(node.props.children as ReactNode)];
}
function render(args: Record<string, unknown> = { caseId: "case-1" }) {
  h.cursor = 0;
  return DisputeConsent({ args, itemId: "widget-1" });
}
function flushEffects() { h.queued.splice(0).forEach(effect => effect()); }
async function settle() { for (let i = 0; i < 12; i++) await Promise.resolve(); }
async function mount(args?: Record<string, unknown>) { render(args); flushEffects(); await settle(); }
function find(label: string) {
  const result = elements(render()).find(element => element.props.children === label);
  if (!result) throw new Error(`Missing ${label}`);
  return result;
}
function click(label: string) { (find(label).props.onClick as () => void)(); }
function waiting(status = "WAITING_USER_APPROVAL") { return { caseId: "case-1", status, reason: "Customer's original reason" }; }
function deferred() {
  let resolve!: (value: unknown) => void;
  let reject!: (cause: unknown) => void;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
function unmount() { h.effects.forEach(effect => effect?.cleanup?.()); }

beforeEach(() => {
  vi.resetAllMocks(); h.states = []; h.refs = []; h.effects = []; h.queued = [];
  h.locked = false; h.sessionKey = 1; oauth.value = null; h.user = { id: "customer", identityVersion: 1 };
  h.get.mockResolvedValue(waiting()); h.respond.mockResolvedValue(waiting("IN_REVIEW"));
});

describe("persisted dispute consent", () => {
  it("disables both consent choices and refuses submission on a locked thread", async () => {
    await mount(); h.locked = true;
    expect(find("Approve dispute review").props.disabled).toBe(true);
    expect(find("Decline dispute review").props.disabled).toBe(true);
    click("Approve dispute review"); click("Decline dispute review"); await settle();
    expect(h.respond).not.toHaveBeenCalled();
  });
  it("loads current state rather than trusting tool status and links the owned case", async () => {
    await mount({ caseId: "case-1", status: "RESOLVED" });
    expect(h.get).toHaveBeenCalledWith("case-1", expect.any(AbortSignal));
    expect(find("Approve dispute review")).toBeTruthy();
    expect(find("View support case").props.to).toBe("/support-cases/case-1");
    expect(elements(render()).some(element => element.props.children === "This records your case decision, not tool permission, a refund, or card protection.")).toBe(true);
  });
  it.each(["OPEN", "IN_REVIEW", "PENDING_EFFECTS", "RESOLVED", "RESOLVED_VALID", "RESOLVED_INVALID"])("does not offer consent for %s", async status => {
    h.get.mockResolvedValue(waiting(status)); await mount();
    expect(elements(render()).some(element => element.props.children === "Approve dispute review")).toBe(false);
    expect(h.respond).not.toHaveBeenCalled();
  });
  it.each([true, false])("records %s once despite a synchronous second click", async approved => {
    await mount(); const pending = deferred(); h.respond.mockReturnValue(pending.promise);
    const approve = find("Approve dispute review").props.onClick as () => void;
    const decline = find("Decline dispute review").props.onClick as () => void;
    (approved ? approve : decline)(); (approved ? decline : approve)();
    expect(h.respond).toHaveBeenCalledOnce();
    expect(h.respond).toHaveBeenCalledWith("case-1", approved, expect.any(AbortSignal));
    expect(find("Refresh").props.disabled).toBe(true);
    pending.resolve(waiting("IN_REVIEW")); await settle();
    expect(find("This case is not awaiting your consent.")).toBeTruthy();
  });
  it("refreshes persisted state after an uncertain POST failure and hides obsolete actions", async () => {
    await mount(); h.respond.mockRejectedValue(new Error("private detail"));
    h.get.mockResolvedValue(waiting("IN_REVIEW")); click("Approve dispute review"); await settle();
    expect(h.get).toHaveBeenCalledTimes(2);
    expect(find("Could not record your response").props.role).toBe("alert");
    expect(find("This case is not awaiting your consent.")).toBeTruthy();
  });
  it("fails closed when mutation error refresh also fails, allowing only refresh", async () => {
    await mount(); h.respond.mockRejectedValue(new ApiError("CASE_VERSION_CONFLICT"));
    h.get.mockRejectedValue(new Error("sensitive")); click("Decline dispute review"); await settle();
    expect(find("Case or evidence changed. Refresh before acting.").props.role).toBe("alert");
    expect(elements(render()).some(element => element.props.children === "Approve dispute review")).toBe(false);
    h.get.mockResolvedValue(waiting()); click("Refresh"); await settle();
    expect(find("Approve dispute review")).toBeTruthy();
  });
  it("refreshes external changes and fetches again on remount", async () => {
    await mount(); h.get.mockResolvedValue(waiting("RESOLVED")); click("Refresh"); await settle();
    expect(find("This case is not awaiting your consent.")).toBeTruthy();
    unmount(); h.states = []; h.refs = []; h.effects = []; await mount();
    expect(h.get).toHaveBeenCalledTimes(3);
  });
  it.each(["get", "respond", "refresh"])("logs out only on active %s AUTH_REQUIRED failures", async phase => {
    if (phase === "get") h.get.mockRejectedValue(new ApiError("AUTH_REQUIRED"));
    await mount();
    if (phase !== "get") {
      h.respond.mockRejectedValue(new ApiError(phase === "respond" ? "AUTH_REQUIRED" : "CASE_VERSION_CONFLICT"));
      if (phase === "refresh") h.get.mockRejectedValue(new ApiError("AUTH_REQUIRED"));
      click("Approve dispute review"); await settle();
    }
    expect(h.logout).toHaveBeenCalledOnce();
  });
  it.each(["get", "respond"])("aborts %s on unmount without stale logout or updates", async phase => {
    const pending = deferred();
    if (phase === "get") h.get.mockReturnValue(pending.promise);
    await mount();
    if (phase === "respond") { h.respond.mockReturnValue(pending.promise); click("Approve dispute review"); }
    const signal = h[phase].mock.calls[0][phase === "get" ? 1 : 2] as AbortSignal;
    const state = [...h.states]; unmount(); pending.reject(new ApiError("AUTH_REQUIRED")); await settle();
    expect(signal.aborted).toBe(true); expect(h.logout).not.toHaveBeenCalled(); expect(h.states).toEqual(state);
  });
  it("hides old-session data immediately and ignores late old-session results before effect cleanup", async () => {
    await mount(); const pending = deferred(); h.respond.mockReturnValue(pending.promise); click("Approve dispute review");
    const signal = h.respond.mock.calls[0][2] as AbortSignal;
    h.sessionKey++; h.user = { id: "other", identityVersion: 2 };
    expect(elements(render()).some(element => element.props.children === "View support case")).toBe(false);
    pending.reject(new ApiError("AUTH_REQUIRED")); await settle(); expect(h.logout).not.toHaveBeenCalled();
    h.get.mockResolvedValue(waiting("IN_REVIEW")); flushEffects(); await settle();
    expect(signal.aborted).toBe(true); expect(find("This case is not awaiting your consent.")).toBeTruthy();
  });
  it.each(["session", "user", "version"])("rejects captured consent handlers after a %s change", async change => {
    await mount();
    const oldAction = find("Approve dispute review").props.onClick as () => void;
    if (change === "session") h.sessionKey++;
    if (change === "user") h.user = { id: "other", identityVersion: 1 };
    if (change === "version") h.user = { id: "customer", identityVersion: 2 };
    render(); oldAction(); expect(h.respond).not.toHaveBeenCalled();
    flushEffects(); await settle(); expect(h.get).toHaveBeenCalledTimes(2);
  });
  it("holds consent unavailable until error recovery finishes", async () => {
    await mount(); const recovery = deferred();
    h.respond.mockRejectedValue(new Error("private")); h.get.mockReturnValue(recovery.promise);
    click("Approve dispute review"); await settle();
    expect(find("Refresh").props.disabled).toBe(true);
    expect(elements(render()).some(element => element.props.children === "Approve dispute review")).toBe(false);
    recovery.resolve(waiting()); await settle(); expect(find("Approve dispute review")).toBeTruthy();
  });
  it.each([{}, { caseId: "" }, { caseId: 3 }])("rejects missing or invalid case args", async args => {
    await mount(args); expect(h.get).not.toHaveBeenCalled(); expect(h.respond).not.toHaveBeenCalled();
  });
  it("does not fetch without a session", async () => { h.user = null; await mount(); expect(h.get).not.toHaveBeenCalled(); });
  it("rejects a response for another case with controlled feedback", async () => {
    h.get.mockResolvedValue({ ...waiting(), caseId: "foreign" }); await mount();
    expect(find("Support case is unavailable").props.role).toBe("alert");
    expect(elements(render()).some(element => element.props.children === "View support case")).toBe(false);
  });
});

describe("OAuth legacy case decisions", () => {
  it("dispatches only an explicit decision with a ready mcp.access grant", async () => {
    oauth.value = { status: { required: true, ready: true }, busy: false };
    await mount();
    expect(h.respond).not.toHaveBeenCalled();
    expect(find("Approve dispute review").props.disabled).toBe(false);
    click("Approve dispute review");
    await settle();
    expect(h.respond).toHaveBeenCalledOnce();
  });
  it.each([null, { required: true, ready: false }])("blocks mutation without a ready grant but preserves owned refresh", async status => {
    oauth.value={status,busy:false}; await mount();
    for(const label of ["Approve dispute review","Decline dispute review"]) {expect(find(label).props.disabled).toBe(true);click(label);}
    await settle(); expect(h.respond).not.toHaveBeenCalled();
    click("Refresh");await settle();expect(h.get).toHaveBeenCalledTimes(2);
  });
});
