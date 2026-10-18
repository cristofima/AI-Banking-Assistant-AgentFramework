import { useTranslation } from "react-i18next";
import { useOAuthReadiness } from "@/context/OAuthReadinessContext";
import { Button } from "@/components/ui/button";

export function OAuthChatGate() {
  const readiness = useOAuthReadiness();
  const { t } = useTranslation();
  if (!readiness || (readiness.status && !readiness.status.required)) return null;
  const { status, busy, error, startLocked, authorize, verify } = readiness;
  return <section className="p-3 space-y-2 border-b" aria-label={t("OAuth chat authorization")}>
    <p role={error ? "alert" : "status"}>{t(error ? "OAuth status unavailable" : !status ? "OAuth checking" : status.ready ? "OAuth chat ready" : "OAuth chat required")}</p>
    {status?.required && <p>{t("OAuth access limits")}</p>}
    {status?.required && !status.ready && <Button disabled={busy || startLocked} onClick={() => void authorize()}>{t("OAuth chat authorize")}</Button>}
    {error && <Button variant="outline" disabled={busy} onClick={() => void verify()}>{t("OAuth check status")}</Button>}
  </section>;
}
