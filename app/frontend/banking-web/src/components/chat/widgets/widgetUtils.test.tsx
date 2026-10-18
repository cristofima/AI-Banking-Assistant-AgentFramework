import { isValidElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useSendWidgetAction, type WidgetActionCallbacks } from "./widgetUtils";
vi.mock("@/context/OAuthReadinessContext", () => ({ useOAuthReadiness: () => null }));
import { ToolApprovalRequest } from "./common/ToolApprovalRequest";

const h = vi.hoisted(() => ({
  slots: [] as unknown[], cursor: 0,
  effects: [] as Array<{ deps: unknown[]; cleanup?: () => void }>,
  queued: [] as Array<() => void>, send: vi.fn(), completed: vi.fn(),
  thread: "thread-1" as string | null, streaming: false,
}));
vi.mock("react", async original => ({
  ...await original<typeof import("react")>(),
  useState: (initial: unknown) => {
    const index = h.cursor++;
    if (!(index in h.slots)) h.slots[index] = typeof initial === "function" ? initial() : initial;
    return [h.slots[index], (next: unknown) => {
      h.slots[index] = typeof next === "function" ? next(h.slots[index]) : next;
    }];
  },
  useRef: (current: unknown) => h.slots[h.cursor++] ??= { current },
  useCallback: (callback: unknown) => callback,
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
vi.mock("@/components/chat/ResponsesChatProvider", () => ({ useChat: () => ({
  activeThreadId: h.thread, sendWidgetAction: h.send, isStreaming: h.streaming,
  isApprovalCompleted: h.completed, isThreadLocked: () => false,
}) }));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

function flushEffects() { h.queued.splice(0).forEach(effect => effect()); }
function WidgetActionHarness(callbacks?: WidgetActionCallbacks) {
  h.cursor = 0;
  const result = useSendWidgetAction(callbacks);
  flushEffects();
  return result;
}
function unmount() { h.effects.forEach(effect => effect?.cleanup?.()); }
function deferred() {
  let resolve!: (outcome: "success" | "error" | "cancelled") => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<"success" | "error" | "cancelled">((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}
const callbacks = () => ({ onThreadStarted: vi.fn(), onThreadEnded: vi.fn(), onError: vi.fn() });
const microtasks = async () => { await Promise.resolve(); await Promise.resolve(); await Promise.resolve(); };

beforeEach(() => {
  vi.clearAllMocks(); h.slots = []; h.effects = []; h.queued = [];
  h.thread = "thread-1"; h.streaming = false;
  h.completed.mockReturnValue(false);
});

describe("widget action hook", () => {
  it("normalizes defaults, suppresses concurrent duplicates and reports only final success", async () => {
    const pending = deferred(); h.send.mockReturnValue(pending.promise);
    const cb = callbacks(); const send = WidgetActionHarness(cb);
    const first = send("approval", { type: "approval" });
    await send("approval", { type: "approval" });
    expect(h.send).toHaveBeenCalledExactlyOnceWith("thread-1", "approval", { type: "approval", payload: {}, handler: "server", loadingBehavior: "auto" });
    expect(cb.onThreadStarted).toHaveBeenCalledOnce();
    expect(cb.onThreadEnded).not.toHaveBeenCalled();
    pending.resolve("success"); await first;
    expect(cb.onThreadEnded).toHaveBeenCalledOnce();
    expect(cb.onError).not.toHaveBeenCalled();
  });
  it.each(["error", "cancelled", "rejection"])("reports controlled %s failure and permits retry", async outcome => {
    const pending = deferred(); h.send.mockReturnValueOnce(pending.promise).mockResolvedValue("success");
    const cb = callbacks(); const send = WidgetActionHarness(cb);
    const first = send("approval", { type: "approval" });
    if (outcome === "rejection") pending.reject(new Error("secret provider detail"));
    else pending.resolve(outcome as "error" | "cancelled");
    await first;
    expect(cb.onError).toHaveBeenCalledExactlyOnceWith({ message: "Approval response not completed" });
    expect(cb.onThreadEnded).not.toHaveBeenCalled();
    await send("approval", { type: "approval", payload: { approved: false }, handler: "client", loadingBehavior: "manual" });
    expect(h.send).toHaveBeenLastCalledWith("thread-1", "approval", { type: "approval", payload: { approved: false }, handler: "client", loadingBehavior: "manual" });
    expect(cb.onThreadStarted).toHaveBeenCalledTimes(2);
    expect(cb.onThreadEnded).toHaveBeenCalledOnce();
  });
  it("refuses actions without an active thread before starting", async () => {
    h.thread = null;
    const cb = callbacks(); await WidgetActionHarness(cb)("approval", { type: "approval" });
    expect(cb.onError).toHaveBeenCalledWith({ message: "No active thread - cannot send widget action", code: "NO_ACTIVE_THREAD" });
    expect(cb.onThreadStarted).not.toHaveBeenCalled();
    expect(h.send).not.toHaveBeenCalled();
  });
  it("uses the latest callbacks after rerender while retaining the pending guard", async () => {
    const pending = deferred(); h.send.mockReturnValue(pending.promise);
    const old = callbacks(); const latest = callbacks();
    const first = WidgetActionHarness(old)("approval", { type: "approval" });
    await WidgetActionHarness(latest)("approval", { type: "approval" });
    pending.resolve("success"); await first;
    expect(h.send).toHaveBeenCalledOnce();
    expect(old.onThreadEnded).not.toHaveBeenCalled();
    expect(latest.onThreadEnded).toHaveBeenCalledOnce();
  });
  it.each(["success", "error", "rejection"])("suppresses %s callbacks after unmount", async outcome => {
    const pending = deferred(); h.send.mockReturnValue(pending.promise);
    const cb = callbacks(); const first = WidgetActionHarness(cb)("approval", { type: "approval" });
    unmount();
    if (outcome === "rejection") pending.reject(new Error("private"));
    else pending.resolve(outcome as "success" | "error");
    await first;
    expect(cb.onThreadEnded).not.toHaveBeenCalled();
    expect(cb.onError).not.toHaveBeenCalled();
  });
});

type ElementProps = { children?: ReactNode; onClick?: () => void; disabled?: boolean; loading?: boolean; role?: string };
function elements(node: ReactNode): Array<React.ReactElement<ElementProps>> {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement<ElementProps>(node)) return [];
  return [node, ...elements(node.props.children)];
}
const args = { tool_name: "open_transaction_dispute", tool_args: { transactionId: "tx-1" }, call_id: "call-1", request_id: "approval-1" };
function card(itemId = "approval-1") {
  h.cursor = 0;
  const tree = ToolApprovalRequest({ args, itemId });
  flushEffects();
  return tree;
}
function buttons(tree = card()) { return elements(tree).filter(element => typeof element.props.onClick === "function"); }
function alerts(tree = card()) { return elements(tree).filter(element => element.props.role === "alert"); }

describe("tool approval card with real action hook", () => {
  it("guards synchronous opposing clicks, shows loading and disables permanently only on success", async () => {
    const pending = deferred(); h.send.mockReturnValue(pending.promise);
    const initial = buttons(); initial[0].props.onClick!(); initial[1].props.onClick!();
    expect(h.send).toHaveBeenCalledExactlyOnceWith("thread-1", "approval-1", {
      type: "approval", payload: { ...args, approved: true }, handler: "server", loadingBehavior: "auto",
    });
    expect(buttons().map(button => [button.props.disabled, button.props.loading])).toEqual([[true, true], [true, false]]);
    pending.resolve("success"); await microtasks();
    expect(buttons().map(button => [button.props.disabled, button.props.loading])).toEqual([[true, false], [true, false]]);
    buttons()[1].props.onClick!(); expect(h.send).toHaveBeenCalledOnce();
    expect(alerts()).toEqual([]);
  });
  it.each(["error", "cancelled", "rejection"])("allows rejection retry after %s without stale loading or alert", async outcome => {
    const pending = deferred(); const retry = deferred();
    h.send.mockReturnValueOnce(pending.promise).mockReturnValueOnce(retry.promise);
    buttons()[0].props.onClick!();
    if (outcome === "rejection") pending.reject(new Error("private"));
    else pending.resolve(outcome as "error" | "cancelled");
    await microtasks();
    expect(alerts()[0].props.children).toBe("Approval response not completed. Please retry.");
    expect(buttons().map(button => [button.props.disabled, button.props.loading])).toEqual([[false, false], [false, false]]);
    buttons()[1].props.onClick!();
    expect(alerts()).toEqual([]);
    expect(buttons().map(button => [button.props.disabled, button.props.loading])).toEqual([[true, false], [true, true]]);
    expect(h.send.mock.calls[1][2].payload.approved).toBe(false);
    retry.resolve("success"); await microtasks();
    expect(buttons().every(button => button.props.disabled)).toBe(true);
  });
  it("starts disabled for an already completed approval on remount", () => {
    h.completed.mockReturnValue(true);
    const controls = buttons();
    expect(h.completed).toHaveBeenCalledWith("thread-1", "approval-1");
    expect(controls.every(button => button.props.disabled)).toBe(true);
    controls[0].props.onClick!(); expect(h.send).not.toHaveBeenCalled();
  });
  it("blocks actions during another stream and reenables when it ends", () => {
    h.streaming = true;
    const controls = buttons();
    expect(controls.every(button => button.props.disabled)).toBe(true);
    controls[0].props.onClick!(); expect(h.send).not.toHaveBeenCalled();
    h.streaming = false;
    expect(buttons().every(button => !button.props.disabled)).toBe(true);
  });
  it("surfaces missing-thread failure and permits action after the thread becomes active", async () => {
    h.thread = null; buttons()[0].props.onClick!(); await microtasks();
    expect(alerts()).toHaveLength(1); expect(h.send).not.toHaveBeenCalled();
    h.thread = "thread-1"; h.send.mockResolvedValue("success");
    buttons()[1].props.onClick!(); await microtasks();
    expect(h.send).toHaveBeenCalledOnce();
    expect(buttons().every(button => button.props.disabled)).toBe(true);
  });
  it("does not update card state after unmount while an approval is pending", async () => {
    const pending = deferred(); h.send.mockReturnValue(pending.promise);
    buttons()[0].props.onClick!(); unmount();
    const stateBefore = [...h.slots];
    pending.resolve("success"); await microtasks();
    expect(h.slots).toEqual(stateBefore);
  });
});
