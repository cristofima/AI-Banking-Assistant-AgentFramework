import { isValidElement, type ReactElement, type ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { OAuthChatGate } from "./OAuthChatGate";
import { ChatShell } from "./chat/ChatShell";
import en from "@/locales/en.json";
import es from "@/locales/es.json";
import pt from "@/locales/pt.json";
const h = vi.hoisted(() => ({ status: null as null | { required: boolean; ready: boolean }, busy: false, error: false, startLocked: false, authorize: vi.fn(), verify: vi.fn(), historyOpen: false }));
vi.mock("@/context/OAuthReadinessContext", () => ({ useOAuthReadiness: () => h }));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock("./chat/ResponsesChatProvider", () => ({ useChat: () => h }));
type Element = ReactElement<Record<string, unknown>>;
function elements(node: ReactNode): Element[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement<Record<string, unknown>>(node)) return [];
  return [node, ...elements(node.props.children as ReactNode)];
}
beforeEach(() => { vi.clearAllMocks(); h.status = null; h.busy = false; h.error = false; h.startLocked = false; h.historyOpen = false; });
describe("shared customer chat gate", () => {
  it("hides the gate for default HMAC mode", () => { h.status = { required: false, ready: false }; expect(OAuthChatGate()).toBeNull(); });
  it("offers only read-only reconciliation after failed status", () => {
    h.error = true;
    const controls = elements(OAuthChatGate());
    expect(controls.find(node => node.props.role === "alert")?.props.children).toBe("OAuth status unavailable");
    expect(controls.some(node => node.props.children === "OAuth chat authorize")).toBe(false);
    (controls.find(node => node.props.children === "OAuth check status")!.props.onClick as () => void)();
    expect(h.verify).toHaveBeenCalledOnce(); expect(h.authorize).not.toHaveBeenCalled();
  });
  it("requires explicit authorization and disables uncertain-start replay", () => {
    h.status = { required: true, ready: false };
    expect(h.authorize).not.toHaveBeenCalled();
    const button = elements(OAuthChatGate()).find(node => node.props.children === "OAuth chat authorize")!;
    (button.props.onClick as () => void)(); expect(h.authorize).toHaveBeenCalledOnce();
    h.startLocked = true;
    expect(elements(OAuthChatGate()).find(node => node.props.children === "OAuth chat authorize")!.props.disabled).toBe(true);
  });
  it.each([false, true])("keeps readiness in shared Support/floating shell including history=%s", historyOpen => {
    h.historyOpen = historyOpen;
    expect(elements(ChatShell({})).filter(node => node.type === OAuthChatGate)).toHaveLength(1);
  });
  it.each([en, es, pt])("includes localized lifecycle and read-only warning keys", catalog => {
    for (const key of Object.keys(en).filter(key => key.startsWith("OAuth "))) expect((catalog as Record<string, string>)[key]).toBeTruthy();
    h.status = { required: true, ready: true };
    expect(elements(OAuthChatGate()).some(node => node.props.children === "OAuth access limits")).toBe(true);
    expect(elements(OAuthChatGate()).some(node => node.props.children === "OAuth chat ready")).toBe(true);
  });
});
