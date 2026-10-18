import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  widgetRegistry,
  type ClientWidgetComponent,
} from "@/components/chat/widgets";
import type { ActionConfig } from "@/components/chat/widgets/types";
import type {
  AttachmentMeta,
  ChatContextValue,
  ComposerConfig,
  RetryConfig,
  ShellContainerConfig,
  StarterPrompt,
  Thread,
  ThreadItem,
  WelcomeHeaderConfig,
} from "./types";
import { useThreadStream, type StreamEvent } from "./useThreadStream";
import { useTranslation } from "react-i18next";
import { readToolCase, readToolPreview, toolOutputFailed, toolProgressKey, type ToolOutputItem } from "./responseItems";

import { useAuth } from "@/context/AuthContext";
import { useOAuthReadiness } from "@/context/OAuthReadinessContext";
import { clearChatSnapshot, markChatInterrupted, persistChatSnapshot, readChatSnapshot, recordedDecisionSchema } from "./sessionHistory";
import { getSupportCase } from "@/api/disputeClient";
import { ApiError } from "@/api/errors";
import { disputePreviewSchema, supportCaseStatusSchema } from "@/api/supportCaseContracts";

const ChatContext = createContext<ChatContextValue | undefined>(undefined);
const generateId = (prefix: string) => `${prefix}_${crypto.randomUUID()}`;
const now = () => new Date().toISOString();

interface ChatProviderProps {
  children: React.ReactNode;
  starterPrompts?: StarterPrompt[];
  chatServerUrl?: string;
  threadListLimit?: number;
  threadListOrder?: "asc" | "desc";
  retryConfig?: RetryConfig;
  attachmentImageSize?: "sm" | "md" | "lg";
  maxVisibleAttachments?: number;
  composerConfig?: ComposerConfig;
  customWidgets?: Record<string, ClientWidgetComponent>;
  welcomeHeaderConfig?: WelcomeHeaderConfig;
  shellContainerConfig?: ShellContainerConfig;
  onAttachmentAdded?: (attachment: AttachmentMeta) => void;
  onAttachmentRemoved?: (attachmentId: string) => void;
  onThreadCreated?: (thread: Thread) => void;
  onThreadStarted?: (threadId: string) => void;
  onThreadItemAdded?: (item: ThreadItem) => void;
  onResponseEnd?: (threadId: string) => void;
  onMessageSent?: (message: {
    text: string;
    attachments?: AttachmentMeta[];
  }) => void;
  onError?: (error: {
    message: string;
    code?: string;
    threadId?: string;
  }) => void;
}

interface ApprovalRequest {
  id: string;
  type: "mcp_approval_request";
  name?: string;
  server_label?: string;
  arguments?: string | Record<string, unknown>;
}

interface ResponsesRequest {
  threadId: string;
  payload: Record<string, unknown>;
  [key: string]: unknown;
}

export function ChatProvider({
  children,
  starterPrompts = [],
  chatServerUrl,
  retryConfig,
  attachmentImageSize = "lg",
  maxVisibleAttachments = 3,
  composerConfig,
  customWidgets,
  welcomeHeaderConfig,
  shellContainerConfig,
  onThreadCreated,
  onThreadStarted,
  onThreadItemAdded,
  onResponseEnd,
  onMessageSent,
  onError,
}: ChatProviderProps) {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [activeThreadId, setActiveThreadId] = useState<string | null>(null);
  const [threadItems, setThreadItems] = useState<Record<string, ThreadItem[]>>(
    {},
  );
  const [currentRequest, setCurrentRequest] = useState<ResponsesRequest | null>(
    null,
  );
  const [isStreaming, setIsStreaming] = useState(false);
  const [hasReceivedStreamEvent, setHasReceivedStreamEvent] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);
  const streamingThreadRef = useRef<string | null>(null);
  const assistantItemsRef = useRef(new Map<string, string>());
  const visibleOutputRef = useRef(false);
  const assistantTextRef = useRef(false);
  const reportedErrorRef = useRef(false);
  const lastMessageRef = useRef<string | null>(null);
  const { t } = useTranslation();
  const toolCallsRef = useRef(new Map<string, { id: string; name: string }>());
  const streamOutcomeRef = useRef<"success" | "error" | "cancelled">("error");
  const actionRef = useRef<((outcome: "success" | "error" | "cancelled") => "success" | "error" | "cancelled") | null>(null);
  const completedApprovalsRef = useRef(new Set<string>());
  const streamFailedRef = useRef(false);
  const continuationsRef = useRef(new Map<string, string>());
  const uncertainThreadsRef = useRef(new Set<string>());
  const recoveryBlockedRef = useRef(new Set<string>());
  const closedThreadsRef = useRef(new Set<string>());
  const furtherHelpRef = useRef(new Set<string>());
  const recordedDecisionsRef = useRef(new Map<string, { caseId: string | null; declined: boolean }>());
  const { user, loading, sessionKey, logout } = useAuth();
  const readiness = useOAuthReadiness();
  const readinessRef = useRef(readiness);
  readinessRef.current = readiness;
  const authorizationPending = useRef(false);
  const recoveryRequestRef = useRef<AbortController | null>(null);
  const scope = user ? JSON.stringify([user.id, user.identityVersion, chatServerUrl]) : null;
  // The auth epoch resets live state, but must not identify persisted reload history.
  const restorationKey = !loading && scope ? JSON.stringify([scope, sessionKey]) : null;
  const [restoredScope, setRestoredScope] = useState<string | null>(null);

  const previousScopeRef = useRef<string | null>(null);
  const previousSessionRef = useRef<number | null>(null);
  useEffect(() => {
    if (loading) {
      setRestoredScope(null);
      return;
    }
    if (!scope || (previousScopeRef.current && previousScopeRef.current !== scope) ||
      (previousSessionRef.current !== null && previousSessionRef.current !== sessionKey)) clearChatSnapshot();
    previousScopeRef.current = scope;
    previousSessionRef.current = sessionKey;
    const recovered = scope ? readChatSnapshot(scope) : null;
    setThreads(recovered?.threads ?? []);
    setActiveThreadId(recovered?.activeThreadId ?? null);
    setThreadItems(recovered?.items ?? {});
    completedApprovalsRef.current = new Set(recovered?.completed ?? []);
    furtherHelpRef.current = new Set(recovered?.threads.filter(thread => thread.metadata?.furtherHelp === true).map(thread => thread.id) ?? []);
    recordedDecisionsRef.current.clear();
    recoveryBlockedRef.current.clear();
    uncertainThreadsRef.current = new Set(recovered?.threads.filter(thread => thread.status.type === "locked").map(thread => thread.id) ?? []);
    closedThreadsRef.current = new Set(recovered?.threads.filter(thread => thread.status.type === "closed").map(thread => thread.id) ?? []);
    continuationsRef.current = new Map(recovered?.threads.flatMap(thread => typeof thread.metadata?.conversationId === "string" ? [[thread.id, thread.metadata.conversationId] as [string, string]] : []) ?? []);
    actionRef.current?.("cancelled");
    actionRef.current = null;
    streamingThreadRef.current = null;
    assistantItemsRef.current.clear();
    toolCallsRef.current.clear();
    lastMessageRef.current = null;
    setCurrentRequest(null);
    setIsStreaming(false);
    setHasReceivedStreamEvent(false);
    setRestoredScope(restorationKey);
  }, [scope, loading, sessionKey, restorationKey]);

  useEffect(() => {
    if (scope && restorationKey && restoredScope === restorationKey) persistChatSnapshot(scope, threads, threadItems, activeThreadId,
      completedApprovalsRef.current, new Set([...uncertainThreadsRef.current, ...recoveryBlockedRef.current]), streamingThreadRef.current);
  }, [scope, restorationKey, restoredScope, threads, threadItems, activeThreadId, isStreaming]);

  const isThreadLocked = (threadId: string) => closedThreadsRef.current.has(threadId) || uncertainThreadsRef.current.has(threadId);
  const closeThread = (threadId: string) => {
    if (streamingThreadRef.current) return;
    closedThreadsRef.current.add(threadId);
    setThreads(previous => previous.map(thread => thread.id === threadId ? { ...thread, status: { type: "closed" } } : thread));
  };
  const setFurtherHelp = useCallback((threadId: string, visible: boolean) => {
    if (visible) furtherHelpRef.current.add(threadId);
    else furtherHelpRef.current.delete(threadId);
    setThreads(previous => previous.map(thread => thread.id === threadId ? { ...thread, metadata: { ...thread.metadata, furtherHelp: visible } } : thread));
  }, []);

  useEffect(() => () => {
    const settle = actionRef.current;
    actionRef.current = null;
    settle?.("cancelled");
  }, []);

  useEffect(() => {
    Object.entries(customWidgets ?? {}).forEach(([name, component]) => {
      widgetRegistry.register(name, component);
    });
  }, [customWidgets]);

  const activeThread = useMemo(
    () => threads.find((thread) => thread.id === activeThreadId),
    [activeThreadId, threads],
  );
  const items = useMemo(
    () => (activeThreadId ? (threadItems[activeThreadId] ?? []) : []),
    [activeThreadId, threadItems],
  );

  const addItem = useCallback(
    (threadId: string, item: ThreadItem) => {
      setThreadItems((previous) => ({
        ...previous,
        [threadId]: (previous[threadId] ?? []).some((existing) => existing.id === item.id)
          ? (previous[threadId] ?? []).map((existing) => existing.id === item.id ? item : existing)
          : [...(previous[threadId] ?? []), item],
      }));
      onThreadItemAdded?.(item);
    },
    [onThreadItemAdded],
  );

  const reportStreamError = useCallback((threadId: string, code = "SERVICE_UNAVAILABLE", allowRetry = false, httpStatus?: number) => {
    if (reportedErrorRef.current) return;
    reportedErrorRef.current = true;
    const message = t(code === "AUTH_REQUIRED" ? "Session expired"
      : code === "ACCESS_DENIED" ? "Access denied" : "The response could not be completed.");
    addItem(threadId, {
      id: generateId("error"), thread_id: threadId, created_at: now(),
      type: "error", code, message, allow_retry: allowRetry, http_status: httpStatus,
    });
    onError?.({ message, code: code === "stream_error" ? "SERVICE_UNAVAILABLE" : code, threadId });
  }, [addItem, onError, t]);

  const handleTextDelta = useCallback((threadId: string, delta: string, outputId?: string) => {
    if (!delta) return;
    const key = outputId === undefined ? "fallback" : `output:${outputId}`;
    const itemId = assistantItemsRef.current.get(key) ?? generateId("assistant");
    assistantItemsRef.current.set(key, itemId);
    if (delta.trim()) {
      visibleOutputRef.current = true;
      assistantTextRef.current = true;
    }
    setThreadItems((previous) => {
      const existing = previous[threadId] ?? [];
      const index = existing.findIndex((item) => item.id === itemId);
      if (index < 0) {
        return {
          ...previous,
          [threadId]: [
            ...existing,
            {
              id: itemId,
              thread_id: threadId,
              created_at: now(),
              type: "assistant_message",
              content: [{ type: "output_text", text: delta, annotations: [] }],
              streaming: true,
            },
          ],
        };
      }

      const updated = [...existing];
      const item = updated[index];
      if (item.type === "assistant_message") {
        updated[index] = {
          ...item,
          content: [{ ...item.content[0], text: item.content[0].text + delta }],
        };
      }
      return { ...previous, [threadId]: updated };
    });
  }, []);

  const handleApprovalRequest = useCallback(
    (threadId: string, request: ApprovalRequest) => {
      let toolArgs: Record<string, unknown> = {};
      if (typeof request.arguments === "string") {
        try {
          toolArgs = JSON.parse(request.arguments) as Record<string, unknown>;
        } catch {
          toolArgs = { raw: request.arguments };
        }
      } else if (request.arguments) {
        toolArgs = request.arguments;
      }

      visibleOutputRef.current = true;
      addItem(threadId, {
        id: request.id,
        thread_id: threadId,
        created_at: now(),
        type: "client_widget",
        name: "tool_approval_request",
        args: {
          tool_name: request.name ?? request.server_label ?? "MCP tool",
          tool_args: toolArgs,
          call_id: request.id,
          request_id: request.id,
        },
      });
    },
    [addItem],
  );

  const handleStreamEvent = useCallback(
    (event: StreamEvent) => {
      const threadId = streamingThreadRef.current;
      if (!threadId) return;
      if (event.type === "response.completed") {
        if (!streamFailedRef.current) streamOutcomeRef.current = "success";
        return;
      }
      if (event.type === "response.incomplete") {
        streamOutcomeRef.current = "error";
        streamFailedRef.current = true;
      }

      if (
        event.type === "response.output_text.delta" &&
        typeof event.delta === "string"
      ) {
        setHasReceivedStreamEvent(true);
        const outputId = typeof event.item_id === "string" ? event.item_id
          : typeof event.message_id === "string" ? event.message_id : undefined;
        handleTextDelta(threadId, event.delta, outputId);
        return;
      }

      if (event.type === "response.output_item.added" || event.type === "response.output_item.done") {
        const item = event.item as ToolOutputItem | undefined;
        if (item?.type === "mcp_approval_request" && item.id) {
          setHasReceivedStreamEvent(true);
          handleApprovalRequest(threadId, item as ApprovalRequest);
          return;
        }
        if (!item) return;
        const callId = item.call_id ?? item.id;
        const key = toolProgressKey(item.name);
        if (event.type === "response.output_item.added" &&
          (item.type === "function_call" || item.type === "mcp_call") && callId && key &&
          !toolCallsRef.current.has(callId)) {
          const taskId = `tool_${threadId}_${callId}`;
          toolCallsRef.current.set(callId, { id: taskId, name: key });
          addItem(threadId, {
            id: taskId, thread_id: threadId, created_at: now(), type: "task",
            task: { type: "custom", title: t(key), status_indicator: "loading" },
          });
        }
        // A call declaration or outputless MCP completion is not a successful result.
        if (event.type === "response.output_item.done" && (item.output !== undefined || item.error)) {
          const failed = Boolean(item.error) || toolOutputFailed(item.output);
          if (failed) {
            streamOutcomeRef.current = "error";
            streamFailedRef.current = true;
          }
          const call = callId ? toolCallsRef.current.get(callId) : undefined;
          if (call) {
            if (failed) {
              addItem(threadId, {
                id: call.id, thread_id: threadId, created_at: now(), type: "task",
                task: { type: "custom", title: t("Tool result unavailable"), status_indicator: "none" },
              });
            } else {
              setThreadItems((previous) => ({
                ...previous,
                [threadId]: (previous[threadId] ?? []).filter((entry) => entry.id !== call.id),
              }));
            }
          }
          const preview = !item.error ? readToolPreview(item.output) : null;
          if (preview && call) {
            visibleOutputRef.current = true;
            addItem(threadId, {
              id: `preview_${threadId}_${call.id}`, thread_id: threadId,
              created_at: now(), type: "client_widget", name: "dispute_preview",
              args: { preview },
            });
          }
          const supportCase = !item.error ? readToolCase(item.output) : null;
          if (supportCase) visibleOutputRef.current = true;
          if (supportCase?.status === "WAITING_USER_APPROVAL") {
            addItem(threadId, {
              id: `consent_${threadId}_${supportCase.caseId}`, thread_id: threadId,
              created_at: now(), type: "client_widget", name: "dispute_consent",
              args: { caseId: supportCase.caseId },
            });
          }
        }
        return;
      }

      if (event.type === "error" || event.type === "response.failed" || event.type === "response.incomplete") {
        streamOutcomeRef.current = "error";
        streamFailedRef.current = true;
        const responseError = (
          event.response as
            | { error?: { message?: string; code?: string } }
            | undefined
        )?.error;
        const receivedCode = event.code ?? responseError?.code;
        uncertainThreadsRef.current.add(threadId);
        const code = receivedCode === "AUTH_REQUIRED" || receivedCode === "ACCESS_DENIED"
          ? receivedCode : "SERVICE_UNAVAILABLE";
        reportStreamError(threadId, code, false,
          typeof event.http_status === "number" ? event.http_status : undefined);
      }
    },
    [addItem, handleApprovalRequest, handleTextDelta, reportStreamError, t],
  );

  const handleConversation = useCallback(
    (threadId: string, conversationId: string) => {
      continuationsRef.current.set(threadId, conversationId);
      setThreads((previous) =>
        previous.map((thread) =>
          thread.id === threadId
            ? { ...thread, metadata: { ...thread.metadata, conversationId } }
            : thread,
        ),
      );
    },
    [],
  );

  const finishStream = useCallback(() => {
    const threadId = streamingThreadRef.current;
    if (!threadId) return;
    if (!visibleOutputRef.current && streamOutcomeRef.current !== "cancelled") {
      streamOutcomeRef.current = "error";
      streamFailedRef.current = true;
      reportStreamError(threadId);
    }
    const assistantItemIds = new Set(assistantItemsRef.current.values());
    if (threadId) {
      setThreadItems((previous) => ({
        ...previous,
        [threadId]: (previous[threadId] ?? []).map((item) =>
          item.type === "task" && item.task.type === "custom" && item.task.status_indicator === "loading"
            ? { ...item, task: { ...item.task, title: t("Tool result unavailable"), status_indicator: "none" } }
            : item,
        ),
      }));
      toolCallsRef.current.clear();
    }
    if (assistantItemIds.size) {
      setThreadItems((previous) => ({
        ...previous,
        [threadId]: (previous[threadId] ?? []).map((item) =>
          assistantItemIds.has(item.id) && item.type === "assistant_message"
            ? { ...item, streaming: false }
            : item,
        ),
      }));
    }
    setIsStreaming(false);
    setCurrentRequest(null);
    assistantItemsRef.current.clear();
    streamingThreadRef.current = null;
    const settle = actionRef.current;
    actionRef.current = null;
    const outcome = settle?.(streamOutcomeRef.current) ?? streamOutcomeRef.current;
    if (outcome === "success") recoveryBlockedRef.current.delete(threadId);
    else {
      recoveryBlockedRef.current.add(threadId);
      uncertainThreadsRef.current.add(threadId);
      setThreads(previous => previous.map(thread => thread.id === threadId && thread.status.type !== "closed"
        ? { ...thread, status: { type: "locked" }, metadata: { ...thread.metadata, interrupted: true } } : thread));
    }
    onResponseEnd?.(threadId);
  }, [onResponseEnd, reportStreamError, t]);

  const { cancel } = useThreadStream({
    url: chatServerUrl ?? "/responses",
    request: currentRequest,
    onEvent: handleStreamEvent,
    onConversation: handleConversation,
    onError: () => {
      streamOutcomeRef.current = "error";
      streamFailedRef.current = true;
      const threadId = streamingThreadRef.current;
      if (threadId) {
        uncertainThreadsRef.current.add(threadId);
        reportStreamError(threadId, "stream_error");
      }
      finishStream();
    },
    onComplete: finishStream,
    enabled: currentRequest !== null,
    retryConfig,
  });

  const submitInput = useCallback(
    async (threadId: string, input: unknown) => {
      if (!scope || streamingThreadRef.current || closedThreadsRef.current.has(threadId)) return;
      if (readinessRef.current) {
        if (authorizationPending.current) return;
        authorizationPending.current = true;
        const authorized = await readinessRef.current.verify();
        authorizationPending.current = false;
        if (!authorized || recoveryScope.current !== restorationKey || recoverySelection.current !== threadId || streamingThreadRef.current || closedThreadsRef.current.has(threadId) || uncertainThreadsRef.current.has(threadId) || furtherHelpRef.current.has(threadId)) {
          actionRef.current?.("error"); actionRef.current = null;
          return;
        }
      }
      streamOutcomeRef.current = "error";
      streamFailedRef.current = false;
      toolCallsRef.current.clear();
      if (uncertainThreadsRef.current.has(threadId)) {
        reportStreamError(threadId);
        return;
      }
      const conversationId = continuationsRef.current.get(threadId);
      markChatInterrupted(scope, threadId);
      streamingThreadRef.current = threadId;
      assistantItemsRef.current.clear();
      visibleOutputRef.current = false;
      assistantTextRef.current = false;
      reportedErrorRef.current = false;
      setIsStreaming(true);
      setHasReceivedStreamEvent(false);
      setCurrentRequest({
        threadId,
        payload: {
          input,
          stream: true,
          ...(typeof conversationId === "string"
            ? { conversation: conversationId }
            : {}),
        },
      });
    },
    [reportStreamError, scope, restorationKey],
  );

  const createLocalThread = useCallback(
    (title: string) => {
      const thread: Thread = {
        id: generateId("thread"),
        title: title.slice(0, 48),
        created_at: now(),
        status: { type: "active" },
      };
      setThreads((previous) => [thread, ...previous]);
      recoverySelection.current = thread.id;
      setActiveThreadId(thread.id);
      onThreadCreated?.(thread);
      onThreadStarted?.(thread.id);
      return thread.id;
    },
    [onThreadCreated, onThreadStarted],
  );

  const sendMessage = useCallback(
    (text: string) => {
      const trimmed = text.trim();
      if (authorizationPending.current || (readinessRef.current && (!readinessRef.current.status || (readinessRef.current.status.required && !readinessRef.current.status.ready)))) return;
      if (!scope || !trimmed || streamingThreadRef.current || (activeThreadId && (closedThreadsRef.current.has(activeThreadId) || furtherHelpRef.current.has(activeThreadId))) || activeThread?.metadata?.furtherHelp === true) return;
      if (activeThreadId && uncertainThreadsRef.current.has(activeThreadId)) {
        reportStreamError(activeThreadId);
        return;
      }
      lastMessageRef.current = trimmed;
      const threadId = activeThreadId ?? createLocalThread(trimmed);

      addItem(threadId, {
        id: generateId("user"),
        thread_id: threadId,
        created_at: now(),
        type: "user_message",
        content: [{ type: "input_text", text: trimmed }],
        attachments: [],
      });
      onMessageSent?.({ text: trimmed });
      submitInput(threadId, [
        { role: "user", content: [{ type: "input_text", text: trimmed }] },
      ]);
    },
    [activeThreadId, activeThread, scope, addItem, createLocalThread, onMessageSent, reportStreamError, submitInput],
  );

  const sendWidgetAction = useCallback(
    (threadId: string, itemId: string, action: ActionConfig): Promise<"success" | "error" | "cancelled"> => {
      const key = `${threadId}:${itemId}`;
      const widget = (threadItems[threadId] ?? []).find((item) => item.id === itemId && item.type === "client_widget");
      const previewDecision = widget?.type === "client_widget" && widget.name === "dispute_preview" && action.type === "dispute_preview_decision";
      const approval = widget?.type === "client_widget" && widget.name === "tool_approval_request" && action.type === "approval";
      if (authorizationPending.current || (readinessRef.current && (!readinessRef.current.status || (readinessRef.current.status.required && (!readinessRef.current.status.ready || approval))))) return Promise.resolve("error");
      if (!scope || (!previewDecision && !approval)) return Promise.resolve("error");
      const proposedDecision = recordedDecisionSchema.safeParse({ caseId: action.payload?.caseId ?? null, declined: action.payload?.declined === true, status: action.payload?.status });
      if (previewDecision && !proposedDecision.success) return Promise.resolve("error");
      const parsedDecision = recordedDecisionSchema.safeParse(widget?.type === "client_widget" ? widget.args.recordedDecision : undefined);
      const recorded = previewDecision ? recordedDecisionsRef.current.get(key) ?? (parsedDecision.success ? parsedDecision.data : undefined) : undefined;
      if (recorded && (recorded.declined !== (action.payload?.declined === true) || recorded.caseId !== (action.payload?.caseId ?? null))) return Promise.resolve("error");
      if (previewDecision && !recorded) {
        recordedDecisionsRef.current.set(key, { caseId: action.payload?.caseId as string ?? null, declined: action.payload?.declined === true });
        setThreadItems(previous => ({ ...previous, [threadId]: (previous[threadId] ?? []).map(item => item.id === itemId && item.type === "client_widget"
          ? { ...item, args: { ...item.args, recordedDecision: { caseId: action.payload?.caseId ?? null, declined: action.payload?.declined === true, status: supportCaseStatusSchema.safeParse(action.payload?.status).success ? action.payload?.status : undefined } } } : item) }));
      }
      if (closedThreadsRef.current.has(threadId) || uncertainThreadsRef.current.has(threadId) || activeThreadId !== threadId || streamingThreadRef.current) return Promise.resolve("error");
      if (completedApprovalsRef.current.has(key)) return Promise.resolve("success");
      const result = new Promise<"success" | "error" | "cancelled">((resolve) => {
        actionRef.current = (outcome) => {
          if (previewDecision && action.payload?.declined !== true && outcome === "success" && !assistantTextRef.current) outcome = "error";
          if (outcome === "success") {
            completedApprovalsRef.current.add(key);
            if (previewDecision && action.payload?.declined !== true && assistantTextRef.current) {
              setFurtherHelp(threadId, true);
            }
          }
          resolve(outcome);
          return outcome;
        };
      });
      submitInput(threadId, previewDecision ? [{
        role: "user", content: [{ type: "input_text", text: action.payload?.declined === true
          ? "I declined the dispute proposal. Do not create a support case."
          : `I explicitly consented to creation and human review. The authenticated application already recorded support case ${action.payload?.caseId}. Do not create another dispute; acknowledge the recorded request without claiming financial effects.` }],
      }] : [{
        type: "mcp_approval_response", approval_request_id: itemId,
        approve: Boolean(action?.payload?.approved),
      }]);
      return result;
    },
    [activeThreadId, scope, setFurtherHelp, submitInput, threadItems],
  );

  const createThread = useCallback(
    (initialMessage?: string) => {
      if (!scope || streamingThreadRef.current || authorizationPending.current) return;
      if (initialMessage && readinessRef.current && (!readinessRef.current.status || (readinessRef.current.status.required && !readinessRef.current.status.ready))) return;
      recoveryRequestRef.current?.abort();
      setHistoryOpen(false);
      setHasReceivedStreamEvent(false);
      if (!initialMessage) {
        setActiveThreadId(null);
        return;
      }

      const threadId = createLocalThread(initialMessage);
      lastMessageRef.current = initialMessage;
      addItem(threadId, {
        id: generateId("user"),
        thread_id: threadId,
        created_at: now(),
        type: "user_message",
        content: [{ type: "input_text", text: initialMessage }],
        attachments: [],
      });
      submitInput(threadId, [
        {
          role: "user",
          content: [{ type: "input_text", text: initialMessage }],
        },
      ]);
    },
    [scope, addItem, createLocalThread, submitInput],
  );

  const recoveryScope = useRef(restorationKey);
  recoveryScope.current = restorationKey;
  const recoverySelection = useRef(activeThreadId);
  recoverySelection.current = activeThreadId;
  useEffect(() => () => { recoveryRequestRef.current?.abort(); }, [restorationKey]);

  const recoverCaseAcknowledgement = async (threadId: string, itemId: string): Promise<boolean> => {
    if (!restorationKey || recoveryRequestRef.current || streamingThreadRef.current || activeThreadId !== threadId) return false;
    const widget = (threadItems[threadId] ?? []).find(item => item.id === itemId && item.type === "client_widget" && item.name === "dispute_preview");
    const parsed = recordedDecisionSchema.safeParse(widget?.type === "client_widget" ? widget.args.recordedDecision : undefined);
    if (!parsed.success || !parsed.data.caseId || parsed.data.declined) return false;
    const decision = parsed.data;
    const controller = new AbortController();
    recoveryRequestRef.current = controller;
    const epoch = restorationKey;
    try {
      const supportCase = await getSupportCase(parsed.data.caseId!, controller.signal);
      const preview = widget?.type === "client_widget" ? disputePreviewSchema.safeParse(widget.args.preview) : null;
      if (controller.signal.aborted || recoveryScope.current !== epoch || recoverySelection.current !== threadId || streamingThreadRef.current || supportCase.caseId !== decision.caseId ||
        (preview?.success && (supportCase.transactionId !== preview.data.transactionId || supportCase.reason !== preview.data.reason))) return false;
      createThread(t("chat.recovery.acknowledgement", { caseId: supportCase.caseId }));
      return recoverySelection.current !== threadId;
    } catch (cause) {
      if (!controller.signal.aborted && recoveryScope.current === epoch && cause instanceof ApiError && ["AUTH_REQUIRED", "AUTH_EXPIRED", "AUTH_INVALID"].includes(cause.code)) logout();
      return false;
    } finally {
      if (recoveryRequestRef.current === controller) recoveryRequestRef.current = null;
    }
  };

  const markPreviewAttempted = (threadId: string, itemId: string): void => {
    if (readinessRef.current && (!readinessRef.current.status || readinessRef.current.status.required)) throw new Error("Read-only OAuth scope");
    const widget = (threadItems[threadId] ?? []).find(item => item.id === itemId);
    if (!scope || restoredScope !== restorationKey || isThreadLocked(threadId) || activeThreadId !== threadId || widget?.type !== "client_widget" || widget.name !== "dispute_preview" || !disputePreviewSchema.safeParse(widget.args.preview).success || widget.args.recoveryOnly || widget.args.recordedDecision) throw new Error("Recovery evidence unavailable");
    const updated = { ...threadItems, [threadId]: (threadItems[threadId] ?? []).map(item => item.id === itemId && item.type === "client_widget"
      ? { ...item, args: { ...item.args, recoveryOnly: true } } : item) };
    if (!persistChatSnapshot(scope, threads, updated, activeThreadId, completedApprovalsRef.current, new Set([...uncertainThreadsRef.current, threadId]), streamingThreadRef.current, { threadId, itemId, previewToken: (widget.args.preview as { previewToken: string }).previewToken })) throw new Error("Recovery evidence unavailable");
    recoveryBlockedRef.current.add(threadId);
    setThreadItems(updated);
  };

  const value: ChatContextValue = {
    threads,
    activeThreadId,
    activeThread,
    items,
    isStreaming,
    hasReceivedStreamEvent,
    historyOpen,
    starterPrompts: readiness && (!readiness.status || (readiness.status.required && !readiness.status.ready) || readiness.busy) ? [] : starterPrompts,
    chatServerUrl,
    progressUpdate: null,
    attachmentImageSize,
    maxVisibleAttachments,
    composerConfig,
    welcomeHeaderConfig,
    shellContainerConfig,
    sendMessage,
    cancelStreaming: () => {
      if (streamingThreadRef.current) uncertainThreadsRef.current.add(streamingThreadRef.current);
      streamOutcomeRef.current = "cancelled";
      cancel();
      finishStream();
    },
    retryLastMessage: () => {
      if (lastMessageRef.current) sendMessage(lastMessageRef.current);
    },
    sendWidgetAction,
    recoverCaseAcknowledgement,
    markPreviewAttempted,
    isApprovalCompleted: (threadId, itemId) => completedApprovalsRef.current.has(`${threadId}:${itemId}`),
    isThreadLocked,
    closeThread,
    setFurtherHelp,
    createThread,
    selectThread: (threadId) => {
      recoveryRequestRef.current?.abort();
      setActiveThreadId(threadId);
      setHistoryOpen(false);
    },
    toggleHistory: () => setHistoryOpen((open) => !open),
    openHistory: () => setHistoryOpen(true),
    closeHistory: () => setHistoryOpen(false),
    getThreadItems: (threadId) => threadItems[threadId] ?? [],
    onThreadCreated,
    onThreadStarted,
    onThreadItemAdded,
    onResponseEnd,
    onMessageSent,
    onError,
  };

  return <ChatContext.Provider value={value}>{children}</ChatContext.Provider>;
}

export const useChat = () => {
  const context = useContext(ChatContext);
  if (!context) throw new Error("useChat must be used within a ChatProvider");
  return context;
};
