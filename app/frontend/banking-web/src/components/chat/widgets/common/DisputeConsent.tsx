import { useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import { getSupportCase, respondToSupportCaseApproval } from "@/api/disputeClient";
import { ApiError, errorTranslationKey } from "@/api/errors";
import { useAuth } from "@/context/AuthContext";
import type { SupportCase } from "@/models/SupportCase";
import type { ClientWidgetProps } from "../WidgetRegistry";
import { useChat } from "../../ResponsesChatProvider";
import { useOAuthReadiness } from "@/context/OAuthReadinessContext";

export function DisputeConsent({ args }: ClientWidgetProps) {
  const { t } = useTranslation();
  const { user, sessionKey, logout } = useAuth();
  const { activeThreadId, isThreadLocked } = useChat();
  const readiness = useOAuthReadiness();
  const locked = Boolean(readiness && (!readiness.status || (readiness.status.required && !readiness.status.ready) || readiness.busy)) || Boolean(activeThreadId && isThreadLocked(activeThreadId));
  const caseId = typeof args.caseId === "string" && args.caseId.trim() ? args.caseId : null;
  const scope = JSON.stringify([caseId, user?.id, user?.identityVersion, sessionKey]);
  const latestScope = useRef(scope);
  latestScope.current = scope;
  const pending = useRef<AbortController | null>(null);
  const [state, setState] = useState<{
    scope: string; supportCase: SupportCase | null; busy: boolean; error: string | null;
  }>({ scope, supportCase: null, busy: true, error: null });
  const current = state.scope === scope ? state : { supportCase: null, busy: true, error: null };

  const run = async (approved?: boolean) => {
    if (!caseId || !user || latestScope.current !== scope || pending.current) return;
    if (approved !== undefined && (locked || state.scope !== scope || state.supportCase?.status !== "WAITING_USER_APPROVAL")) return;
    const controller = new AbortController();
    pending.current = controller;
    const active = () => !controller.signal.aborted && latestScope.current === scope;
    setState({ scope, supportCase: null, busy: true, error: null });
    let supportCase: SupportCase | null = null;
    let error: string | null = null;
    const fetchCase = async () => {
      const result = await getSupportCase(caseId, controller.signal);
      if (result.caseId !== caseId) throw new ApiError("SERVICE_UNAVAILABLE");
      return result;
    };
    try {
      supportCase = approved === undefined
        ? await fetchCase()
        : await respondToSupportCaseApproval(caseId, approved, controller.signal);
      if (supportCase.caseId !== caseId) throw new ApiError("SERVICE_UNAVAILABLE");
    } catch (cause: unknown) {
      if (!active()) return;
      supportCase = null;
      error = errorTranslationKey(cause, approved === undefined ? "Support case is unavailable" : "Could not record your response");
      if (cause instanceof ApiError && cause.code === "AUTH_REQUIRED") {
        logout();
        return;
      }
      // A failed POST may have persisted; reload before offering consent again.
      if (approved !== undefined) {
        try {
          supportCase = await fetchCase();
        } catch (refreshCause: unknown) {
          supportCase = null;
          if (!active()) return;
          if (refreshCause instanceof ApiError && refreshCause.code === "AUTH_REQUIRED") {
            logout();
            return;
          }
        }
      }
    } finally {
      if (active()) {
        pending.current = null;
        setState({ scope, supportCase, busy: false, error });
      }
    }
  };

  useEffect(() => {
    pending.current?.abort();
    pending.current = null;
    setState({ scope, supportCase: null, busy: true, error: null });
    void run();
    return () => {
      pending.current?.abort();
      pending.current = null;
    };
    // The request lifetime follows identity and case scope, not fetched state.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scope]);

  if (!caseId || !user) return <div role="alert">{t("Support case is unavailable")}</div>;

  return (
    <section className="rounded-lg border bg-card p-4 space-y-3" aria-label={t("Dispute consent")} aria-busy={current.busy}>
      <h3 className="font-semibold">{t("Dispute consent")}</h3>
      <p className="text-sm">{t("Approve to continue reviewing this dispute, or decline to withdraw it.")}</p>
      <p className="text-sm text-muted-foreground">{t("This records your case decision, not tool permission, a refund, or card protection.")}</p>
      {current.busy && <output className="block">{t("Loading support case...")}</output>}
      {current.error && <div role="alert">{t(current.error)}</div>}
      {current.supportCase && (
        <>
          <p className="text-sm">{t("Reason")}: {current.supportCase.reason}</p>
          <p className="text-sm">{t("Status")}: {t(`support-cases.status.${current.supportCase.status}`, { keySeparator: ".", defaultValue: t("Unavailable") })}</p>
          {current.supportCase.status === "WAITING_USER_APPROVAL" ? (
            <div className="flex flex-wrap gap-2">
              <Button disabled={current.busy || locked} onClick={() => void run(true)}>{t("Approve dispute review")}</Button>
              <Button variant="outline" disabled={current.busy || locked} onClick={() => void run(false)}>{t("Decline dispute review")}</Button>
            </div>
          ) : <p className="text-sm">{t("This case is not awaiting your consent.")}</p>}
        </>
      )}
      <div className="flex flex-wrap items-center gap-3">
        <Button variant="outline" disabled={current.busy} onClick={() => void run()}>{t("Refresh")}</Button>
        {current.supportCase && <Link className="text-sm underline" to={`/support-cases/${encodeURIComponent(caseId)}`}>{t("View support case")}</Link>}
      </div>
    </section>
  );
}
