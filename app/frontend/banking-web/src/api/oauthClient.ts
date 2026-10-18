import { z } from "zod";
import { getAuthToken } from "@/api/authToken";
import { ApiError, readApiError } from "@/api/errors";

const baseUrl = import.meta.env.VITE_RESPONSES_BFF_URL || "";
const protocolKeys = ["client_id", "redirect_uri", "response_type", "scope", "state", "code_challenge", "code_challenge_method"] as const;
const callbackKeys = new Set(["code", "state", "error", "error_description", "error_uri", "access_token", "id_token", "token_type", "refresh_token", "client_secret", "code_verifier"]);

function hasControlCharacters(value: string): boolean {
  return Array.from(value).some((character) => character.charCodeAt(0) < 32 || character.charCodeAt(0) === 127);
}

function queryPairs(query: string): Array<[string, string]> {
  if (!query) return [];
  return query.split("&").map((field) => {
    const separator = field.indexOf("=");
    if (separator < 1) throw new ApiError("INVALID_REQUEST");
    try {
      return [decodeURIComponent(field.slice(0, separator).replace(/\+/g, " ")),
        decodeURIComponent(field.slice(separator + 1).replace(/\+/g, " "))];
    } catch {
      throw new ApiError("INVALID_REQUEST");
    }
  });
}

function callbackParts(value: string): { target: string; pairs: Array<[string, string]> } {
  if (/[\s\\#]/.test(value) || hasControlCharacters(value)) throw new ApiError("INVALID_REQUEST");
  const match = value.match(/^https:\/\/([^/?]+)([^?]*)(?:\?(.*))?$/);
  if (!match) throw new ApiError("INVALID_REQUEST");
  let url: URL;
  try { url = new URL(value); }
  catch { throw new ApiError("INVALID_REQUEST"); }
  const target = `https://${match[1]}${match[2]}`;
  if (!url.hostname || url.username || url.password || match[1].includes("@")
    || match[1].includes("%") || url.pathname !== (match[2] || "/")) {
    throw new ApiError("INVALID_REQUEST");
  }
  return { target, pairs: queryPairs(match[3] || "") };
}

function isCallback(value: string): boolean {
  try {
    const { pairs } = callbackParts(value);
    return new Set(pairs.map(([key]) => key)).size === pairs.length
      && pairs.every(([key, value]) => !callbackKeys.has(key) && !hasControlCharacters(key) && !hasControlCharacters(value));
  } catch { return false; }
}

export const oauthRequestSchema = z.object({
  client_id: z.string().min(1).max(128).regex(/^[A-Za-z0-9._~-]+$/),
  redirect_uri: z.string().min(1).max(2048).refine(isCallback),
  response_type: z.literal("code"),
  scope: z.literal("mcp.access"),
  state: z.string().min(1).max(512).regex(/^[\x21-\x7e]+$/),
  code_challenge: z.string().regex(/^[A-Za-z0-9_-]{43}$/),
  code_challenge_method: z.literal("S256"),
}).strict();
export type OAuthRequest = z.infer<typeof oauthRequestSchema>;

export function parseOAuthQuery(search: string): OAuthRequest | null {
  if (!search.startsWith("?") || search.length > 4096 || search.includes("#") || hasControlCharacters(search)) return null;
  try {
    const pairs = queryPairs(search.slice(1));
    if (pairs.length !== protocolKeys.length || new Set(pairs.map(([key]) => key)).size !== pairs.length) return null;
    const parsed = oauthRequestSchema.safeParse(Object.fromEntries(pairs));
    return parsed.success ? parsed.data : null;
  } catch { return null; }
}

export function validateOAuthRedirect(value: unknown, request: OAuthRequest, approved: boolean): string {
  const parsed = z.object({ redirect_uri: z.string().min(1).max(4096) }).strict().safeParse(value);
  if (!parsed.success) throw new ApiError("SERVICE_UNAVAILABLE");
  try {
    const expected = callbackParts(request.redirect_uri);
    const actual = callbackParts(parsed.data.redirect_uri);
    if (actual.target !== expected.target || actual.pairs.length <= expected.pairs.length
      || expected.pairs.some(([key, entry], index) => actual.pairs[index]?.[0] !== key || actual.pairs[index]?.[1] !== entry)) {
      throw new ApiError("SERVICE_UNAVAILABLE");
    }
    const protocol = actual.pairs.slice(expected.pairs.length);
    const fields = Object.fromEntries(protocol);
    const allowed = approved ? ["state", "code"] : ["state", "error", "error_description", "error_uri"];
    if (new Set(actual.pairs.map(([key]) => key)).size !== actual.pairs.length
      || protocol.some(([key, entry]) => !allowed.includes(key) || !entry || hasControlCharacters(entry))
      || fields.state !== request.state || (approved ? !fields.code : fields.error !== "access_denied")) {
      throw new ApiError("SERVICE_UNAVAILABLE");
    }
    return parsed.data.redirect_uri;
  } catch { throw new ApiError("SERVICE_UNAVAILABLE"); }
}

export const oauthStatusSchema = z.object({ required: z.boolean(), ready: z.boolean() }).strict();
export type OAuthStatus = z.infer<typeof oauthStatusSchema>;
const callbackSchema = z.object({
  state: z.string().min(1).max(512).regex(/^[\x21-\x7e]+$/),
  code: z.string().min(1).max(4096).regex(/^[\x21-\x7e]+$/),
}).strict();

export function parseOAuthCallback(search: string): { state: string; code: string } | "denied" | null {
  if (!search.startsWith("?") || search.length > 8192 || search.includes("#") || hasControlCharacters(search)) return null;
  try {
    const pairs = queryPairs(search.slice(1));
    if (new Set(pairs.map(([key]) => key)).size !== pairs.length) return null;
    const fields = Object.fromEntries(pairs);
    if (fields.error === "access_denied" && pairs.every(([key, value]) => ["state", "error", "error_description", "error_uri"].includes(key) && !hasControlCharacters(value))
      && callbackSchema.shape.state.safeParse(fields.state).success) return "denied";
    const parsed = callbackSchema.safeParse(fields);
    return parsed.success ? parsed.data : null;
  } catch { return null; }
}

export function validateOAuthStart(body: unknown): string {
  const parsed = z.object({ authorize_url: z.string().max(4120) }).strict().safeParse(body);
  if (!parsed.success) throw new ApiError("SERVICE_UNAVAILABLE");
  const value = parsed.data.authorize_url;
  if (!value.startsWith("/oauth/consent?") || /[\\\s#]/.test(value) || hasControlCharacters(value)
    || !parseOAuthQuery(value.slice("/oauth/consent".length))) throw new ApiError("SERVICE_UNAVAILABLE");
  const url = new URL(value, window.location.origin);
  if (url.origin !== window.location.origin || url.pathname !== "/oauth/consent") throw new ApiError("SERVICE_UNAVAILABLE");
  return value;
}

async function oauthLifecycle(path: string, signal: AbortSignal, payload?: unknown): Promise<unknown> {
  signal.throwIfAborted();
  const token = getAuthToken();
  if (!token) throw new ApiError("AUTH_REQUIRED");
  const response = await fetch(`${baseUrl}/oauth/${path}`, {
    method: path === "status" ? "GET" : "POST",
    headers: { Authorization: `Bearer ${token}`, ...(payload === undefined ? {} : { "Content-Type": "application/json" }) },
    ...(payload === undefined ? {} : { body: JSON.stringify(payload) }), signal, cache: "no-store", redirect: "error",
  });
  signal.throwIfAborted();
  if (!response.ok) throw await readApiError(response);
  let body: unknown;
  try { body = await response.json(); }
  catch { signal.throwIfAborted(); throw new ApiError("SERVICE_UNAVAILABLE"); }
  signal.throwIfAborted();
  if (getAuthToken() !== token) throw new ApiError("AUTH_REQUIRED");
  return body;
}

export async function getOAuthStatus(signal: AbortSignal): Promise<OAuthStatus> {
  const parsed = oauthStatusSchema.safeParse(await oauthLifecycle("status", signal));
  if (!parsed.success) throw new ApiError("SERVICE_UNAVAILABLE");
  return parsed.data;
}

export async function startOAuth(signal: AbortSignal): Promise<string> {
  return validateOAuthStart(await oauthLifecycle("start", signal));
}

export async function completeOAuth(payload: { state: string; code: string }, signal: AbortSignal): Promise<void> {
  const parsed = callbackSchema.safeParse(payload);
  if (!parsed.success) throw new ApiError("INVALID_REQUEST");
  const body = await oauthLifecycle("callback", signal, parsed.data);
  if (!z.object({ ready: z.literal(true) }).strict().safeParse(body).success) throw new ApiError("SERVICE_UNAVAILABLE");
}

export async function authorizeOAuth(request: OAuthRequest, approved: boolean, signal: AbortSignal): Promise<string> {
  const payload = oauthRequestSchema.safeParse(request);
  if (!payload.success || typeof approved !== "boolean") throw new ApiError("INVALID_REQUEST");
  signal.throwIfAborted();
  const token = getAuthToken();
  if (!token) throw new ApiError("AUTH_REQUIRED");
  const response = await fetch(`${baseUrl}/oauth/authorize`, {
    method: "POST", headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
    body: JSON.stringify({ ...payload.data, approved }), signal, cache: "no-store", redirect: "error",
  });
  signal.throwIfAborted();
  if (!response.ok) throw await readApiError(response);
  let body: unknown;
  try { body = await response.json(); }
  catch { signal.throwIfAborted(); throw new ApiError("SERVICE_UNAVAILABLE"); }
  signal.throwIfAborted();
  if (getAuthToken() !== token) throw new ApiError("AUTH_REQUIRED");
  return validateOAuthRedirect(body, payload.data, approved);
}
