import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { authorizeOAuth, parseOAuthQuery } from "@/api/oauthClient";
import { getAuthToken } from "@/api/authToken";
import { ApiError, errorTranslationKey } from "@/api/errors";
import { useAuth } from "@/context/AuthContext";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";

const OAuthConsent = () => {
  const { user, loading, sessionKey, logout } = useAuth();
  const { search, hash } = useLocation();
  const { t } = useTranslation();
  const request = hash ? null : parseOAuthQuery(search);
  const scope = JSON.stringify([sessionKey, user?.id, user?.role, user?.customerId, user?.identityVersion, loading, search, hash]);
  const currentScope = useRef(scope);
  currentScope.current = scope;
  const pending = useRef<AbortController | null>(null);
  const attempted = useRef(false);
  const mounted = useRef(false);
  const [status, setStatus] = useState<"ready" | "submitting" | "locked" | "redirecting">("ready");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; pending.current?.abort(); };
  }, [scope]);

  const submit = async (approved: boolean): Promise<void> => {
    if (!mounted.current || currentScope.current !== scope || !request || !user || user.role !== "customer" || loading || attempted.current) return;
    attempted.current = true;
    const controller = new AbortController();
    pending.current = controller;
    const token = getAuthToken();
    const isCurrent = () => !controller.signal.aborted && currentScope.current === scope && getAuthToken() === token;
    setStatus("submitting");
    try {
      const callback = await authorizeOAuth(request, approved, controller.signal);
      if (!isCurrent()) return;
      setStatus("redirecting");
      window.location.assign(callback);
    } catch (failure) {
      if (!isCurrent()) return;
      setStatus("locked");
      setError(errorTranslationKey(failure, "OAuth consent uncertain"));
      if (failure instanceof ApiError && failure.code === "AUTH_REQUIRED") logout();
    }
  };

  const disabled = status !== "ready" || attempted.current || loading || user?.role !== "customer";
  return (
    <main className="min-h-screen flex items-center justify-center bg-background p-6">
      <Card className="w-full max-w-xl">
        <CardHeader><CardTitle><h1>{t("OAuth consent title")}</h1></CardTitle></CardHeader>
        <CardContent className="space-y-6">
          <p>{t("OAuth prototype notice")}</p>
          {!request ? <p role="alert">{t("OAuth invalid request")}</p> : <>
            <p>{t("OAuth consent description")}</p>
            <dl className="space-y-2 break-all">
              <dt className="font-semibold">{t("OAuth client")}</dt><dd>{request.client_id}</dd>
              <dt className="font-semibold">{t("OAuth callback")}</dt><dd>{request.redirect_uri}</dd>
              <dt className="font-semibold">{t("OAuth permissions")}</dt><dd>{t("OAuth MCP access")}</dd>
              <dt className="font-semibold">{t("OAuth signed in")}</dt><dd>{user?.email}</dd>
            </dl>
            <p>{t("OAuth access limits")}</p>
            {error && <p role="alert">{t(error)}</p>}
            {status === "submitting" && <p role="status">{t("OAuth submitting")}</p>}
            {status === "redirecting" && <p role="status">{t("OAuth redirecting")}</p>}
            <div className="flex flex-wrap gap-3">
              <Button disabled={disabled} onClick={() => void submit(true)}>{t("OAuth approve")}</Button>
              <Button variant="outline" disabled={disabled} onClick={() => void submit(false)}>{t("OAuth deny")}</Button>
            </div>
          </>}
          <Button variant="ghost" onClick={() => { pending.current?.abort(); logout(); }}>{t("Sign out")}</Button>
        </CardContent>
      </Card>
    </main>
  );
};

export default OAuthConsent;
