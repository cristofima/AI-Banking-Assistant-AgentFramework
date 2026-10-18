import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { getOAuthStatus, startOAuth, type OAuthStatus } from "@/api/oauthClient";
import { getAuthToken } from "@/api/authToken";
import { ApiError } from "@/api/errors";
import { useAuth } from "@/context/AuthContext";

interface OAuthReadiness {
  status: OAuthStatus | null;
  busy: boolean;
  error: boolean;
  startLocked: boolean;
  verify: () => Promise<boolean>;
  authorize: () => Promise<void>;
}
const OAuthReadinessContext = createContext<OAuthReadiness | null>(null);

export function OAuthReadinessProvider({ children }: { children: ReactNode }) {
  const { user, loading, sessionKey, logout } = useAuth();
  const scope = JSON.stringify([user?.id, user?.role, user?.customerId, user?.identityVersion, sessionKey, loading]);
  const currentScope = useRef(scope);
  currentScope.current = scope;
  const pending = useRef<AbortController | null>(null);
  const starting = useRef<string | null>(null);
  const [result, setResult] = useState<{ scope: string; token: string | null; status: OAuthStatus } | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(false);
  const status = result?.scope === scope && result.token === getAuthToken() ? result.status : null;

  const verify = useCallback(async (): Promise<boolean> => {
    if (!user || user.role !== "customer" || loading || pending.current) return false;
    const controller = new AbortController();
    pending.current = controller;
    const token = getAuthToken();
    setBusy(true);
    try {
      const value = await getOAuthStatus(controller.signal);
      if (controller.signal.aborted || currentScope.current !== scope || getAuthToken() !== token) return false;
      setResult({ scope, token, status: value });
      setError(false);
      return !value.required || value.ready;
    } catch (cause) {
      if (!controller.signal.aborted && currentScope.current === scope) {
        setResult(null); setError(true);
        if (cause instanceof ApiError && cause.code === "AUTH_REQUIRED") logout();
      }
      return false;
    } finally {
      if (pending.current === controller) { pending.current = null; setBusy(false); }
    }
  }, [user, loading, scope, logout]);

  const authorize = useCallback(async (): Promise<void> => {
    if (!user || user.role !== "customer" || loading || pending.current || starting.current === scope || !status?.required) return;
    starting.current = scope;
    const controller = new AbortController();
    pending.current = controller;
    const token = getAuthToken();
    setBusy(true); setError(false);
    try {
      const url = await startOAuth(controller.signal);
      if (!controller.signal.aborted && currentScope.current === scope && getAuthToken() === token) window.location.assign(url);
    } catch (cause) {
      if (!controller.signal.aborted && currentScope.current === scope) {
        setError(true);
        if (cause instanceof ApiError && cause.code === "AUTH_REQUIRED") logout();
      }
    } finally {
      if (pending.current === controller) { pending.current = null; setBusy(false); }
    }
  }, [user, loading, scope, status, logout]);

  useEffect(() => {
    void verify();
    const onFocus = () => { void verify(); };
    window.addEventListener("focus", onFocus);
    // This is a read-only status check, never grant renewal or request replay.
    const timer = setInterval(onFocus, 30_000);
    return () => {
      pending.current?.abort(); pending.current = null;
      window.removeEventListener("focus", onFocus); clearInterval(timer);
    };
  }, [verify]);

  return <OAuthReadinessContext.Provider value={{ status, busy, error, startLocked: starting.current === scope, verify, authorize }}>{children}</OAuthReadinessContext.Provider>;
}

export const useOAuthReadiness = () => useContext(OAuthReadinessContext);
