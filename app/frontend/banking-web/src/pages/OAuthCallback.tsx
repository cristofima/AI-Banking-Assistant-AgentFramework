import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Link, Navigate, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { completeOAuth, parseOAuthCallback } from "@/api/oauthClient";
import { getAuthToken } from "@/api/authToken";
import { ApiError } from "@/api/errors";
import { roleHome } from "@/api/roleRoutes";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";

export default function OAuthCallback() {
  const { user, loading, sessionKey, logout } = useAuth();
  const { search, hash } = useLocation();
  const { t } = useTranslation();
  const payload = useRef(hash ? null : parseOAuthCallback(search));
  const scope = JSON.stringify([sessionKey, user?.id, user?.role, user?.customerId, user?.identityVersion, loading]);
  const initialScope = useRef<string | null>(null);
  if (!loading && user && initialScope.current === null) initialScope.current = scope;
  const currentScope = useRef(scope);
  currentScope.current = scope;
  const attempted = useRef(false);
  const pending = useRef<AbortController | null>(null);
  const [status, setStatus] = useState<"ready" | "pending" | "success" | "uncertain">("ready");

  useLayoutEffect(() => {
    // Drop protocol material before any login navigation; never put it in router state.
    window.history.replaceState(null, "", "/oauth/callback");
  }, []);
  useEffect(() => () => { pending.current?.abort(); }, [scope]);

  async function exchange(): Promise<void> {
    const request = payload.current;
    if (!user || user.role !== "customer" || loading || initialScope.current !== scope || attempted.current || !request || request === "denied") return;
    attempted.current = true;
    payload.current = null;
    const controller = new AbortController();
    pending.current = controller;
    const token = getAuthToken();
    setStatus("pending");
    try {
      await completeOAuth(request, controller.signal);
      if (!controller.signal.aborted && currentScope.current === scope && getAuthToken() === token) setStatus("success");
    } catch (cause) {
      if (controller.signal.aborted || currentScope.current !== scope || getAuthToken() !== token) return;
      setStatus("uncertain");
      if (cause instanceof ApiError && cause.code === "AUTH_REQUIRED") logout();
    }
  }

  if (loading) return <p role="status">{t("Restoring your workspace...")}</p>;
  if (!user) return <Navigate to="/login" replace />;
  if (user.role !== "customer") return <Navigate to={roleHome(user.role)} replace />;
  const valid = payload.current && payload.current !== "denied" && initialScope.current === scope;
  const message = status === "success" ? "OAuth callback complete" : status === "uncertain" ? "OAuth callback uncertain"
    : status === "pending" ? "OAuth submitting" : payload.current === "denied" ? "OAuth callback denied" : !valid ? "OAuth invalid request" : "OAuth callback confirm";
  return <main className="min-h-screen flex items-center justify-center p-6"><section className="max-w-xl space-y-4">
    <h1 className="text-xl font-semibold">{t("OAuth callback title")}</h1>
    <p role={status === "uncertain" || (!valid && status === "ready") ? "alert" : "status"}>{t(message)}</p>
    <p>{t("OAuth access limits")}</p>
    <div className="flex flex-wrap gap-3">
      {valid && status === "ready" && <Button onClick={() => void exchange()}>{t("OAuth callback finish")}</Button>}
      <Button asChild variant={status === "success" ? "default" : "outline"}>
        <Link to="/support">{t("OAuth return chat")}</Link>
      </Button>
    </div>
  </section></main>;
}
