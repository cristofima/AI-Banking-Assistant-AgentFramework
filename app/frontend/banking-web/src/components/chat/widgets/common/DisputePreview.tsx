import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { DisputePreviewConsent } from "@/components/DisputePreviewConsent";
import { disputePreviewSchema, supportCaseStatusSchema } from "@/api/supportCaseContracts";
import { Link } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { useChat } from "../../ResponsesChatProvider";
import type { ClientWidgetProps } from "../WidgetRegistry";
import { recordedDecisionSchema, visibleConversation } from "../../sessionHistory";
import { useOAuthReadiness } from "@/context/OAuthReadinessContext";

export function DisputePreview({ args, itemId }: ClientWidgetProps) {
  const { t } = useTranslation();
  const readiness = useOAuthReadiness();
  const authorizationBlocked = Boolean(readiness && (!readiness.status || (readiness.status.required && !readiness.status.ready) || readiness.busy));
  const { activeThreadId, items, isStreaming, sendWidgetAction, isThreadLocked, markPreviewAttempted, recoverCaseAcknowledgement } = useChat();
  const locked = Boolean(activeThreadId && isThreadLocked(activeThreadId));
  const originalThread = useRef(activeThreadId);
  const pending = useRef(false);
  const [recovering, setRecovering] = useState(false);
  const [failed, setFailed] = useState(false);
  const preview = disputePreviewSchema.safeParse(args.preview);
  const parsedDecision = recordedDecisionSchema.safeParse(args.recordedDecision);
  const decision = parsedDecision.success ? parsedDecision.data : undefined;
  const unavailable = isStreaming || !activeThreadId || activeThreadId !== originalThread.current;
  async function continueChat(caseId: string | null, declined: boolean, status?: string) {
    if (!originalThread.current) return;
    try {
      const outcome = await sendWidgetAction(originalThread.current, itemId, {
        type: "dispute_preview_decision", payload: { caseId, declined, status },
      });
      setFailed(outcome !== "success");
    } catch { setFailed(true); }
  }
  async function recover() {
    if (pending.current || unavailable || !originalThread.current) return;
    pending.current = true; setRecovering(true); setFailed(false);
    try { setFailed(!await recoverCaseAcknowledgement(originalThread.current, itemId)); }
    catch { setFailed(true); }
    finally { pending.current = false; setRecovering(false); }
  }
  if (decision) return <section className="rounded-lg border p-4 space-y-3">
    <p>{t(decision.declined ? "Dispute proposal declined" : "Dispute request recorded")}</p>
    {supportCaseStatusSchema.safeParse(decision.status).success && <p>{t(`support-cases.status.${decision.status}`, { keySeparator: "." })}</p>}
    {!decision.declined && decision.caseId && <>
      <Link to={`/support-cases/${encodeURIComponent(decision.caseId)}`}>{t("View support case")}</Link>
      {(locked || failed) && <Button disabled={unavailable || recovering} onClick={() => void recover()}>{t(recovering ? "chat.recovery.loading" : "chat.recovery.continue")}</Button>}
    </>}
    {failed && <p role="alert">{t("Dispute chat continuation unavailable")}</p>}
    {recovering && <p role="status">{t("chat.recovery.loading")}</p>}
  </section>;
  if (!preview.success) return <p>{t("Dispute preview unavailable")}</p>;
  return <DisputePreviewConsent preview={preview.data} conversationHistory={visibleConversation(items)} disabled={authorizationBlocked || locked || unavailable}
    recoveryDisabled={unavailable} recoveryOnly={args.recoveryOnly === true}
    onAttempt={() => { if (!originalThread.current) throw new Error("Recovery evidence unavailable"); markPreviewAttempted(originalThread.current, itemId); }}
    onAccepted={(supportCase) => void continueChat(supportCase.caseId, false, supportCase.status)}
    onDeclined={() => void continueChat(null, true)} />;
}
