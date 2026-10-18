import { isValidElement, type ReactElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { DisputePreview } from "./DisputePreview";
import { DisputePreviewConsent } from "@/components/DisputePreviewConsent";

const h = vi.hoisted(() => ({ cursor: 0, states: [] as unknown[], refs: [] as Array<{ current: unknown }>, thread: "original", items: [] as import("../../types").ThreadItem[], locked: false, streaming: false, send: vi.fn(), attempt: vi.fn(), recover: vi.fn() }));
vi.mock("react", async original => ({
  ...await original<typeof import("react")>(),
  useState: (initial: unknown) => {
    const index = h.cursor++;
    if (!(index in h.states)) h.states[index] = initial;
    return [h.states[index], (value: unknown) => { h.states[index] = value; }];
  },
  useRef: (initial: unknown) => h.refs[h.cursor++] ??= { current: initial },
}));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
const oauth = vi.hoisted(() => ({ value: null as null | { status: { required: boolean; ready: boolean } | null } }));
vi.mock("@/context/OAuthReadinessContext", () => ({ useOAuthReadiness: () => oauth.value }));
vi.mock("../../ResponsesChatProvider", () => ({ useChat: () => ({ activeThreadId: h.thread, items: h.items, isThreadLocked: () => h.locked, isStreaming: h.streaming, sendWidgetAction: h.send, markPreviewAttempted: h.attempt, recoverCaseAcknowledgement: h.recover }) }));
const preview = { previewToken: "signed", transactionId: "tx", reason: "Original reason", expiresAt: "2026-10-04T10:10:00Z", transaction: { id: "tx" } };
type Element = ReactElement<Record<string, unknown>>;
function elements(node: ReactNode): Element[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement<Record<string, unknown>>(node)) return [];
  return [node, ...elements(node.props.children as ReactNode)];
}
function render(args: Record<string, unknown> = { preview }) { h.cursor = 0; return DisputePreview({ args, itemId: "proposal" }); }
function consent() { return elements(render()).find(element => element.type === DisputePreviewConsent)!; }
async function settle() { for (let i = 0; i < 12; i++) await Promise.resolve(); }
beforeEach(() => { oauth.value = null; h.cursor = 0; h.states = []; h.refs = []; h.thread = "original"; h.items = []; h.locked = false; h.streaming = false; h.send.mockReset().mockResolvedValue("success"); h.attempt.mockReset(); h.recover.mockReset().mockResolvedValue(true); });

describe("chat dispute preview", () => {
  it.each([true, false])("continues a recorded decision (%s) without exposing the preview token", async declined => {
    const control = consent();
    if (declined) (control.props.onDeclined as () => void)();
    else (control.props.onAccepted as (value: unknown) => void)({ caseId: "recorded" });
    await settle();
    expect(h.send).toHaveBeenCalledWith("original", "proposal", { type: "dispute_preview_decision", payload: { caseId: declined ? null : "recorded", declined, status: undefined } });
    expect(JSON.stringify(h.send.mock.calls)).not.toContain("signed");
  });
  it("recovers a recorded case without replaying the failed continuation", async () => {
    h.send.mockResolvedValueOnce("error");
    (consent().props.onAccepted as (value: unknown) => void)({ caseId: "recorded" }); await settle();
    const args = { recordedDecision: { caseId: "recorded", declined: false, status: "IN_REVIEW" } };
    const recovery = elements(render(args)).find(element => element.props.children === "chat.recovery.continue")!;
    (recovery.props.onClick as () => void)(); await settle();
    expect(h.recover).toHaveBeenCalledWith("original", "proposal");
    expect(h.send).toHaveBeenCalledTimes(1);
    expect(elements(render(args)).some(element => element.type === DisputePreviewConsent)).toBe(false);
    expect(elements(render(args)).some(element => element.props.role === "alert")).toBe(false);
  });
  it("retains a late receipt on its original thread and disables recovery elsewhere", async () => {
    h.send.mockResolvedValue("error");
    const original = consent(); h.thread = "other";
    expect(consent().props.disabled).toBe(true);
    (original.props.onAccepted as (value: unknown) => void)({ caseId: "recorded" }); await settle();
    expect(h.send).toHaveBeenCalledWith("original", "proposal", { type: "dispute_preview_decision", payload: { caseId: "recorded", declined: false, status: undefined } });
    const recovery = elements(render({ recordedDecision: { caseId: "recorded", declined: false } })).find(element => element.props.children === "chat.recovery.continue")!;
    expect(recovery.props.disabled).toBe(true);
    (recovery.props.onClick as () => void)(); await settle();
    expect(h.recover).not.toHaveBeenCalled();
  });
  it("allows case recovery but not consent on a locked thread", async () => {
    h.locked = true;
    expect(consent().props.disabled).toBe(true);
    expect(consent().props.recoveryDisabled).toBe(false);
    const args = { recordedDecision: { caseId: "recorded", declined: false } };
    const recovery = elements(render(args)).find(element => element.props.children === "chat.recovery.continue")!;
    expect(recovery.props.disabled).toBe(false);
    h.recover.mockRejectedValueOnce(new Error("controlled"));
    (recovery.props.onClick as () => void)(); await settle();
    expect(elements(render(args)).some(element => element.props.role === "alert")).toBe(true);
    expect(h.send).not.toHaveBeenCalled();
  });
  it("keeps recovery single-flight", async () => {
    h.locked = true;
    let finish!: (value: boolean) => void;
    h.recover.mockImplementationOnce(() => new Promise<boolean>(resolve => { finish = resolve; }));
    const recovery = elements(render({ recordedDecision: { caseId: "recorded", declined: false } })).find(element => element.props.children === "chat.recovery.continue")!;
    (recovery.props.onClick as () => void)();
    (recovery.props.onClick as () => void)();
    expect(h.recover).toHaveBeenCalledTimes(1);
    finish(true); await settle();
  });
  it("persists attempted evidence before consent dispatch", () => {
    (consent().props.onAttempt as () => void)();
    expect(h.attempt).toHaveBeenCalledWith("original", "proposal");
    h.attempt.mockImplementationOnce(() => { throw new Error("storage"); });
    expect(() => (consent().props.onAttempt as () => void)()).toThrow("storage");
    expect(h.send).not.toHaveBeenCalled();
  });
  it("rejects malformed receipts and preserves recovery-only proposals", () => {
    expect(elements(render({ recordedDecision: { caseId: "", declined: false } })).some(element => element.props.children === "Dispute preview unavailable")).toBe(true);
    const control = elements(render({ preview, recoveryOnly: true })).find(element => element.type === DisputePreviewConsent)!;
    expect(control.props.recoveryOnly).toBe(true);
  });
  it("forwards only bounded visible messages to REST consent", () => {
    h.items = Array.from({ length: 110 }, (_, index) => ({ id: String(index), thread_id: "original", created_at: "today", type: "user_message", content: [{ type: "input_text", text: String(index) }], attachments: [] }));
    h.items.push({ id: "tool", thread_id: "original", created_at: "today", type: "client_widget", name: "tool_approval_request", args: { secret: "hidden" } });
    expect(consent().props.conversationHistory).toEqual(Array.from({ length: 100 }, (_, index) => ({ role: "user", text: String(index + 10) })));
  });
  it("disables consent during streaming", () => { h.streaming = true; expect(consent().props.disabled).toBe(true); });
});

it.each([null, { required: true, ready: false }])("blocks consent without a ready OAuth grant but preserves recovery", status => {
  oauth.value = { status };
  expect(consent().props.disabled).toBe(true);
  expect(consent().props.recoveryDisabled).toBe(false);
});

it("allows explicit consent with a ready mcp.access grant", () => {
  oauth.value = { status: { required: true, ready: true } };
  expect(consent().props.disabled).toBe(false);
});
