import { afterEach, describe, expect, it, vi } from "vitest";
import { openConsentPopup, registerConsentLink, setConsentClosedHandler } from "./consentPopup";

function stubWindow(open: () => Window | null) {
  const openSpy = vi.fn(open);
  vi.stubGlobal("window", {
    open: openSpy,
    setInterval: (fn: () => void, ms: number) => setInterval(fn, ms),
    clearInterval: (id: number) => clearInterval(id),
  });
  return openSpy;
}

describe("consent popup", () => {
  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
    setConsentClosedHandler(null);
  });

  it("ignores links that were not issued as consent challenges", () => {
    const open = stubWindow(() => null);
    expect(openConsentPopup("https://example.test/other")).toBe(false);
    expect(open).not.toHaveBeenCalled();
  });

  it("notifies once when the popup closes", () => {
    vi.useFakeTimers();
    const popup = { closed: false } as Window;
    stubWindow(() => popup);
    const handler = vi.fn();
    setConsentClosedHandler(handler);
    registerConsentLink("https://consent.example.test/a");
    expect(openConsentPopup("https://consent.example.test/a")).toBe(true);
    vi.advanceTimersByTime(1000);
    expect(handler).not.toHaveBeenCalled();
    Object.assign(popup, { closed: true });
    vi.advanceTimersByTime(2000);
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it("falls back to normal navigation when the popup is blocked", () => {
    stubWindow(() => null);
    registerConsentLink("https://consent.example.test/b");
    expect(openConsentPopup("https://consent.example.test/b")).toBe(false);
  });
});
