const consentLinks = new Set<string>();
let closedHandler: (() => void) | null = null;
let watchTimer: number | undefined;

export function registerConsentLink(link: string): void {
  consentLinks.add(link);
}

export function setConsentClosedHandler(handler: (() => void) | null): void {
  closedHandler = handler;
}

// Returns true when the link was opened in a popup; the caller keeps the default navigation otherwise.
export function openConsentPopup(href: string | undefined): boolean {
  if (!href || !consentLinks.has(href)) return false;
  const popup = window.open(href, "banking-consent", "popup,width=520,height=720");
  if (!popup) return false;
  // The named window is reused, so only the latest popup is watched.
  window.clearInterval(watchTimer);
  watchTimer = window.setInterval(() => {
    if (!popup.closed) return;
    window.clearInterval(watchTimer);
    closedHandler?.();
  }, 500);
  return true;
}
