import type { UserRole } from "./authClient";
import { parseOAuthQuery } from "./oauthClient";

export function savedLoginDestination(state: unknown): string | undefined {
  if (!state || typeof state !== "object" || !("from" in state)) return undefined;
  const from = state.from;
  if (!from || typeof from !== "object" || !("pathname" in from) || typeof from.pathname !== "string") return undefined;
  if (from.pathname === "/oauth/callback") return undefined;
  if (from.pathname !== "/oauth/consent") return from.pathname;
  if (!("search" in from) || typeof from.search !== "string" || ("hash" in from && from.hash)) return undefined;
  return parseOAuthQuery(from.search) ? from.pathname + from.search : undefined;
}

export function roleHome(role: UserRole): string {
  switch (role) {
    case "customer": return "/";
    case "admin": return "/admin/operators";
    case "operator": return "/operator";
  }
}

export function loginDestination(role: UserRole, requested?: string): string {
  if (role !== "customer") return roleHome(role);
  if (requested?.startsWith("/oauth/consent?") && parseOAuthQuery(requested.slice("/oauth/consent".length))) return requested;
  if (requested && /^(\/(credit-cards|portfolio|analytics|account|support)\/?|\/support-cases(?:\/[A-Za-z0-9_-]+)?\/?|\/)$/.test(requested)) {
    return requested;
  }
  const productMatch = requested?.match(/^\/product\/((?:[A-Za-z0-9_.-]|%[A-Fa-f0-9]{2})+)\/?$/);
  if (productMatch) {
    try {
      const id = decodeURIComponent(productMatch[1]);
      const hasControlCharacters = Array.from(id).some((character) => character.charCodeAt(0) < 32);
      if (requested && id !== "." && id !== ".." && !/[\\/?#]/.test(id) && !hasControlCharacters) return requested;
    } catch {
      return roleHome(role);
    }
  }
  return roleHome(role);
}
