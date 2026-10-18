import { isValidElement, type ReactElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ToolApprovalRequest } from "./ToolApprovalRequest";

const h = vi.hoisted(() => ({ locked: false, streaming: false, completed: false, pending: { current: false }, send: vi.fn() }));
vi.mock("react", async original => ({
  ...await original<typeof import("react")>(),
  useState: (initial: unknown) => [typeof initial === "function" ? initial() : initial, vi.fn()],
  useRef: () => h.pending,
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
const oauth = vi.hoisted(() => ({ value: null as null | { status: { required: boolean; ready: boolean } | null; busy: boolean } }));
vi.mock("@/context/OAuthReadinessContext", () => ({ useOAuthReadiness: () => oauth.value }));
vi.mock("../../ResponsesChatProvider", () => ({ useChat: () => ({ activeThreadId: "thread", isThreadLocked: () => h.locked, isStreaming: h.streaming, isApprovalCompleted: () => h.completed }) }));
vi.mock("../widgetUtils", () => ({ useSendWidgetAction: () => h.send, formatAsPython: () => "{}", createPythonCodeBlock: (value: string) => value }));
type Element = ReactElement<Record<string, unknown>>;
function elements(node: ReactNode): Element[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement<Record<string, unknown>>(node)) return [];
  return [node, ...elements(node.props.children as ReactNode)];
}
function controls() {
  return elements(ToolApprovalRequest({ itemId: "approval", args: { tool_name: "getBalance", tool_args: {}, call_id: "call", request_id: "request" } })).filter(element => ["Approve", "Reject"].includes(element.props.children as string));
}
beforeEach(() => { vi.clearAllMocks(); h.locked = false; h.streaming = false; h.completed = false; h.pending.current = false; oauth.value = null; });
describe("tool approval thread guards", () => {
  it.each(["locked", "streaming", "completed"] as const)("blocks both decisions for %s", mode => {
    h[mode] = true;
    for (const button of controls()) {
      expect(button.props.disabled).toBe(true);
      (button.props.onClick as () => void)();
    }
    expect(h.send).not.toHaveBeenCalled();
  });
  it("submits one decision and synchronously prevents conflicting clicks", () => {
    const buttons = controls();
    (buttons[0].props.onClick as () => void)();
    (buttons[1].props.onClick as () => void)();
    expect(h.send).toHaveBeenCalledOnce();
    expect(h.send).toHaveBeenCalledWith("approval", { type: "approval", payload: { tool_name: "getBalance", tool_args: {}, approved: true, call_id: "call", request_id: "request" } });
  });
});

describe("OAuth tool approvals", () => {
  it("requires explicit approval even with a ready mcp.access grant", () => {
    oauth.value = { status: { required: true, ready: true }, busy: false };
    const button = controls()[0];
    expect(button.props.disabled).toBe(false);
    expect(h.send).not.toHaveBeenCalled();
    (button.props.onClick as () => void)();
    expect(h.send).toHaveBeenCalledOnce();
  });
  it.each([null, { required: true, ready: false }])("blocks actions without a ready grant", status => {
    oauth.value={status,busy:false};
    for(const button of controls()) {expect(button.props.disabled).toBe(true);(button.props.onClick as ()=>void)();}
    expect(h.send).not.toHaveBeenCalled();
  });
});
