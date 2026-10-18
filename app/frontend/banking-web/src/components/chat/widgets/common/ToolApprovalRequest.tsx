import React, { useRef, useState } from "react";
import { useChat } from "../../ResponsesChatProvider";
import { Info } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { ClientWidgetProps } from "../WidgetRegistry";
import {
  useSendWidgetAction,
  formatAsPython,
  createPythonCodeBlock,
} from "../widgetUtils";
import ReactMarkdown from "react-markdown";
import { useTranslation } from "react-i18next";
import { useOAuthReadiness } from "@/context/OAuthReadinessContext";

/**
 * Arguments expected by the ToolApprovalRequest widget
 */
interface ToolApprovalArgs extends Record<string, unknown> {
  tool_name: string;
  tool_args: Record<string, unknown>;
  call_id: string;
  request_id: string;
  title?: string;
  description?: string;
}

/**
 * Pre-built widget for tool approval requests
 * This component displays a tool call approval UI with approve/reject buttons
 */
export function ToolApprovalRequest({ args, itemId }: ClientWidgetProps) {
  const { t } = useTranslation();
  const { tool_name, tool_args, call_id, request_id, title, description } =
    args as ToolApprovalArgs;

  // State to track which button is loading (null = none, 'approve' or 'reject')
  const [loadingButton, setLoadingButton] = useState<
    "approve" | "reject" | null
  >(null);
  const { activeThreadId, isApprovalCompleted, isStreaming, isThreadLocked } = useChat();
  const readiness = useOAuthReadiness();
  const locked = Boolean(readiness && (!readiness.status || (readiness.status.required && !readiness.status.ready) || readiness.busy)) || Boolean(activeThreadId && isThreadLocked(activeThreadId));
  const [isDisabled, setIsDisabled] = useState(() =>
    Boolean(activeThreadId && isApprovalCompleted(activeThreadId, itemId)),
  );
  const [failed, setFailed] = useState(false);
  const pendingRef = useRef(false);

  const sendWidgetAction = useSendWidgetAction({
    onThreadEnded: () => {
      pendingRef.current = false;
      setIsDisabled(true);
      setLoadingButton(null);
    },
    onError: () => {
      pendingRef.current = false;
      setFailed(true);
      setIsDisabled(false);
      setLoadingButton(null);
    },
  });

  // Format tool arguments as Python code
  const argsStr = formatAsPython(tool_args);
  const codeBlock = createPythonCodeBlock(argsStr);

  const handleResponse = (approved: boolean) => {
    if (pendingRef.current || isDisabled || isStreaming || locked) return;
    pendingRef.current = true;
    setFailed(false);
    setLoadingButton(approved ? "approve" : "reject");
    void sendWidgetAction(itemId, {
      type: "approval",
      payload: { tool_name, tool_args, approved, call_id, request_id },
    });
  };

  return (
    <Card className="border p-0">
      {/* Header with icon and title */}
      <div className="flex flex-col items-center gap-4 p-4">
        <div className="flex items-center justify-center rounded-full bg-yellow-400 p-3">
          <Info className="h-12 w-12 text-white" />
        </div>
        <div className="flex flex-col items-center gap-1">
          <h3 className="text-xl font-semibold">
            {!title || title === "Approval Required"
              ? t("Approval required")
              : title}
          </h3>
          <p className="text-sm text-muted-foreground">
            {!description ||
            description ===
              "This action requires your approval before proceeding."
              ? t("This action requires your approval before proceeding.")
              : description}
          </p>
          <div className="mt-1 prose prose-sm dark:prose-invert">
            <ReactMarkdown>{`**${tool_name}**`}</ReactMarkdown>
          </div>
        </div>
      </div>

      {/* Tool arguments */}
      <div className="px-4 prose prose-sm dark:prose-invert max-w-none">
        <ReactMarkdown>{codeBlock}</ReactMarkdown>
      </div>

      {/* Divider */}
      <div className="px-4 py-2">
        <Separator />
      </div>

      {failed && <p role="alert" className="px-4 pb-4 text-sm text-destructive">{t("Approval response not completed. Please retry.")}</p>}
      <p className="px-4 pb-4 text-sm text-muted-foreground">{t("Tool permission is separate from dispute consent.")}</p>
      {/* Action buttons */}
      <div className="flex gap-2 p-4 pt-0">
        <Button
          onClick={() => handleResponse(true)}
          className="flex-1"
          disabled={locked || isDisabled || isStreaming || loadingButton !== null}
          loading={loadingButton === "approve"}
        >
          {t("Approve")}
        </Button>
        <Button
          onClick={() => handleResponse(false)}
          variant="outline"
          className="flex-1"
          disabled={locked || isDisabled || isStreaming || loadingButton !== null}
          loading={loadingButton === "reject"}
        >
          {t("Reject")}
        </Button>
      </div>
    </Card>
  );
}
