import { useMemo, useState } from "react";
import { ArrowUp, Loader2, Square } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useChat } from "./ResponsesChatProvider";
import { useTranslation } from "react-i18next";
import { useOAuthReadiness } from "@/context/OAuthReadinessContext";

type ButtonSize = "sm" | "md" | "lg";

interface ComposerProps {
  placeholder?: string;
  buttonSize?: ButtonSize;
}

const BUTTON_DIMENSIONS = {
  sm: { button: "h-8 w-8", icon: "h-3 w-3" },
  md: { button: "h-10 w-10", icon: "h-4 w-4" },
  lg: { button: "h-12 w-12", icon: "h-5 w-5" },
} as const;

export function Composer({ placeholder, buttonSize = "lg" }: ComposerProps) {
  const { t } = useTranslation();
  const { sendMessage, cancelStreaming, isStreaming, activeThreadId, activeThread, isThreadLocked, closeThread, setFurtherHelp, createThread } = useChat();
  const readiness = useOAuthReadiness();
  const authorizationBlocked = Boolean(readiness && (!readiness.status || (readiness.status.required && !readiness.status.ready) || readiness.busy));
  const locked = authorizationBlocked || Boolean(activeThreadId && isThreadLocked(activeThreadId));
  const furtherHelp = activeThread?.metadata?.furtherHelp === true;
  const [value, setValue] = useState("");
  const dimensions = BUTTON_DIMENSIONS[buttonSize];
  const canSubmit = useMemo(() => Boolean(value.trim()), [value]);

  const handleSend = () => {
    if (!canSubmit || isStreaming || locked || furtherHelp) return;
    sendMessage(value);
    setValue("");
  };

  return (
    <div className="px-4 py-3">
      {locked && !authorizationBlocked && <div className="mb-3 space-y-2">
        <p role="status">{t(activeThread?.status.type === "closed" ? "Conversation closed" : activeThread?.metadata?.interrupted ? "chat.recovery.interrupted" : "Recovered conversation is read-only. Start a new conversation to continue.")}</p>
        <Button disabled={isStreaming} onClick={() => { setValue(""); createThread(); }}>{t("chat.recovery.new")}</Button>
      </div>}
      {furtherHelp && !isStreaming && !locked && activeThreadId && <div className="mb-4 space-y-3" role="group" aria-label={t("Can I help with anything else?")}>
        <p>{t("Can I help with anything else?")}</p>
        <div className="flex flex-col gap-4 sm:flex-row sm:gap-6">
          <Button variant="default" onClick={() => setFurtherHelp(activeThreadId, false)}>{t("Continue chatting")}</Button>
          <Button variant="destructive" onClick={() => closeThread(activeThreadId)}>{t("Close conversation")}</Button>
        </div>
      </div>}
      <div className="flex items-end gap-3 rounded-2xl border border-border/50 bg-muted/20 px-3 py-2 shadow-inner">
        <Textarea
          disabled={locked || furtherHelp}
          value={value}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              handleSend();
            }
          }}
          placeholder={placeholder ?? t("Type your message...")}
          className="min-h-[64px] max-h-40 flex-1 resize-none border-none bg-transparent px-0 py-2 text-sm shadow-none focus-visible:ring-0 focus-visible:ring-offset-0"
        />
        {isStreaming && (
          <Button
            type="button"
            size="icon"
            onClick={cancelStreaming}
            className={`${dimensions.button} rounded-full bg-destructive text-destructive-foreground shadow-sm`}
          >
            <Square className={dimensions.icon} />
            <span className="sr-only">{t("Stop streaming")}</span>
          </Button>
        )}
        <Button
          type="button"
          size="icon"
          onClick={handleSend}
          className={`${dimensions.button} rounded-full border border-primary/20 bg-primary/10 text-primary shadow-sm disabled:border-transparent disabled:bg-muted-foreground/50 disabled:text-white`}
          disabled={isStreaming || !canSubmit || locked || furtherHelp}
        >
          {isStreaming ? (
            <Loader2 className={`${dimensions.icon} animate-spin`} />
          ) : (
            <ArrowUp className={dimensions.icon} />
          )}
          <span className="sr-only">{t("Send message")}</span>
        </Button>
      </div>
    </div>
  );
}
