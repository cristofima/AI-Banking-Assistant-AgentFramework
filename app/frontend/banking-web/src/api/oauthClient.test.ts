import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { authorizeOAuth, parseOAuthQuery, validateOAuthRedirect } from "./oauthClient";
import { ApiError } from "./errors";

const request = {
  client_id: "registered-client", redirect_uri: "https://client.example/callback?tenant=demo",
  response_type: "code" as const, scope: "mcp.access" as const, state: "original-state",
  code_challenge: "a".repeat(43), code_challenge_method: "S256" as const,
};
const search = "?" + new URLSearchParams(request).toString();
const success = { redirect_uri: request.redirect_uri + "&code=opaque-code&state=original-state" };
const fetchMock = vi.fn();
let token: string | null;

beforeEach(() => {
  token = "synthetic-application-jwt";
  vi.stubGlobal("localStorage", { getItem: vi.fn(() => token) });
  vi.stubGlobal("fetch", fetchMock);
  fetchMock.mockReset().mockResolvedValue(new Response(JSON.stringify(success)));
});
afterEach(() => vi.unstubAllGlobals());

describe("bounded OAuth query", () => {
  it("parses the complete request", () => expect(parseOAuthQuery(search)).toEqual(request));
  it.each(["https://CLIENT.example:443/callback", "https://client.example:8443/callback", "https://client.example"])("preserves valid exact HTTPS callback %s", (redirect_uri) => {
    const value = { ...request, redirect_uri };
    expect(parseOAuthQuery("?" + new URLSearchParams(value))).toEqual(value);
    const returned = redirect_uri + "?code=x&state=original-state";
    expect(validateOAuthRedirect({ redirect_uri: returned }, value, true)).toBe(returned);
  });
  it.each(Object.keys(request))("requires %s", (key) => {
    const query = new URLSearchParams(request); query.delete(key);
    expect(parseOAuthQuery("?" + query)).toBeNull();
  });
  it.each([search + "&state=second", search + "&%73tate=second", search + "&extra=x", search + "&", search + "&bad", search.replace("original-state", "%ff"), search.replace("original-state", "%"), search + "#fragment", "?" + "x".repeat(4096)])("rejects ambiguous or malformed query %s", (query) => expect(parseOAuthQuery(query)).toBeNull());
  it.each([
    ["client_id", "a".repeat(129)], ["client_id", "bad client"], ["state", ""], ["state", "a".repeat(513)],
    ["state", "space here"], ["scope", "mcp.access offline_access"], ["response_type", "token"],
    ["code_challenge", "a".repeat(42)], ["code_challenge", "a".repeat(44)], ["code_challenge", "!".repeat(43)],
    ["code_challenge_method", "plain"], ["redirect_uri", "https://client.example/" + "a".repeat(2048)],
  ])("rejects invalid %s", (key, value) => {
    expect(parseOAuthQuery("?" + new URLSearchParams({ ...request, [key]: value }))).toBeNull();
  });
  it.each(["http://client.example/callback", "//client.example/callback", "https://user:password@client.example/callback", "https://client.example/callback#", "https://client.example\\evil/callback", "https://client.example:bad/callback", "https://%63lient.example/callback", "https://client.example/callback?x=%00", "https://client.example/a/../callback", "https://client.example/callback?x=1&x=2", "https://client.example/callback?state=x", "https://client.example/callback?access_token=x", "https://client.example/callback?x=%ff", "https://client.example/callback?x=1 "])("rejects unsafe callback %s", (redirect_uri) => {
    expect(parseOAuthQuery("?" + new URLSearchParams({ ...request, redirect_uri }))).toBeNull();
  });
});

describe("exact OAuth authorization response", () => {
  it("accepts code approval and explicit denial preserving state and callback query", () => {
    expect(validateOAuthRedirect(success, request, true)).toBe(success.redirect_uri);
    const denial = request.redirect_uri + "&error=access_denied&error_description=Denied&state=original-state";
    expect(validateOAuthRedirect({ redirect_uri: denial }, request, false)).toBe(denial);
  });
  it.each([
    "https://evil.example/callback?tenant=demo&code=x&state=original-state",
    "https://client.example/other?tenant=demo&code=x&state=original-state",
    "https://client.example/callback?tenant=other&code=x&state=original-state",
    "https://client.example/callback?code=x&tenant=demo&state=original-state",
    request.redirect_uri + "&code=x&state=wrong",
    request.redirect_uri + "&code=x&state=original-state&state=original-state",
    request.redirect_uri + "&code=x&state=original-state&tenant=demo",
    request.redirect_uri + "&code=x&state=original-state&extra=x",
    request.redirect_uri + "&code=x&state=original-state&access_token=token",
    request.redirect_uri + "&code=&state=original-state",
    request.redirect_uri + "&code=x&state=original-state&error=access_denied",
    request.redirect_uri + "&code=x&state=original-state&error_description=x",
    request.redirect_uri + "&error=access_denied&state=original-state",
    request.redirect_uri + "&code=x&state=original-state#fragment",
    request.redirect_uri + "&code=%ff&state=original-state",
    request.redirect_uri + "&code=%00&state=original-state",
  ])("rejects mismatched approval %s", (redirect_uri) => {
    expect(() => validateOAuthRedirect({ redirect_uri }, request, true)).toThrow(ApiError);
  });
  it.each(["&code=x&state=original-state", "&error=server_error&state=original-state", "&error=access_denied", "&error=access_denied&state=original-state&code=x"])("rejects invalid denial %s", (suffix) => {
    expect(() => validateOAuthRedirect({ redirect_uri: request.redirect_uri + suffix }, request, false)).toThrow(ApiError);
  });
  it.each([null, {}, { ...success, access_token: "token" }, { redirect_uri: 123 }])("rejects malformed response", (body) => {
    expect(() => validateOAuthRedirect(body, request, true)).toThrow(ApiError);
  });
});

describe("BFF-only OAuth consent API", () => {
  it.each([true, false])("sends explicit decision %s with application JWT only in headers", async (approved) => {
    const redirect_uri = request.redirect_uri + (approved ? "&code=x" : "&error=access_denied") + "&state=original-state";
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ redirect_uri })));
    const signal = new AbortController().signal;
    expect(await authorizeOAuth(request, approved, signal)).toBe(redirect_uri);
    expect(fetchMock).toHaveBeenCalledWith(expect.stringMatching(/\/oauth\/authorize$/), {
      method: "POST", headers: { Authorization: "Bearer synthetic-application-jwt", "Content-Type": "application/json" },
      body: JSON.stringify({ ...request, approved }), signal, cache: "no-store", redirect: "error",
    });
    expect(fetchMock.mock.calls[0][0]).not.toContain(token);
    expect(fetchMock.mock.calls[0][1].body).not.toContain(token);
  });
  it("does not dispatch without JWT or with invalid input", async () => {
    token = null;
    await expect(authorizeOAuth(request, true, new AbortController().signal)).rejects.toMatchObject({ code: "AUTH_REQUIRED" });
    token = "synthetic";
    await expect(authorizeOAuth({ ...request, scope: "other" } as typeof request, true, new AbortController().signal)).rejects.toMatchObject({ code: "INVALID_REQUEST" });
    expect(fetchMock).not.toHaveBeenCalled();
  });
  it("rejects pre-aborted and stale identity responses", async () => {
    const controller = new AbortController(); controller.abort();
    await expect(authorizeOAuth(request, true, controller.signal)).rejects.toBeDefined();
    expect(fetchMock).not.toHaveBeenCalled();
    fetchMock.mockImplementation(async () => { token = "other-token"; return new Response(JSON.stringify(success)); });
    await expect(authorizeOAuth(request, true, new AbortController().signal)).rejects.toMatchObject({ code: "AUTH_REQUIRED" });
  });
  it.each([401, 403, 500])("maps HTTP %s without leaking server details or retrying", async (status) => {
    fetchMock.mockResolvedValue(new Response(JSON.stringify({ detail: "sensitive server detail" }), { status }));
    await expect(authorizeOAuth(request, true, new AbortController().signal)).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledOnce();
  });
  it("rejects unreadable JSON and aborted body reads", async () => {
    fetchMock.mockResolvedValue(new Response("not json"));
    await expect(authorizeOAuth(request, true, new AbortController().signal)).rejects.toMatchObject({ code: "SERVICE_UNAVAILABLE" });
    const controller = new AbortController();
    fetchMock.mockResolvedValue({ ok: true, json: async () => { controller.abort(); return success; } });
    await expect(authorizeOAuth(request, true, controller.signal)).rejects.toBeDefined();
  });
});


describe("OAuth lifecycle contracts", () => {
  it.each([
    ["status", {required:false, ready:false}, "GET", undefined],
    ["callback", {ready:true}, "POST", {state:"s",code:"c"}],
  ])("uses JWT-only no-store/no-redirect %s", async (kind, response, method, payload) => {
    const {getOAuthStatus, completeOAuth} = await import("./oauthClient");
    fetchMock.mockResolvedValue(new Response(JSON.stringify(response)));
    const signal = new AbortController().signal;
    if (kind === "status") await getOAuthStatus(signal); else await completeOAuth(payload as {state:string;code:string}, signal);
    expect(fetchMock).toHaveBeenCalledOnce();
    expect(fetchMock.mock.calls[0][0]).toMatch(new RegExp("/oauth/"+kind+"$"));
    expect(fetchMock.mock.calls[0][1]).toMatchObject({method, cache:"no-store", redirect:"error", signal});
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer " + token);
    expect(fetchMock.mock.calls[0][1].body).toBe(payload ? JSON.stringify(payload) : undefined);
  });
  it("only starts at the exact relative validated consent route", async () => {
    const {startOAuth, validateOAuthStart}=await import("./oauthClient");
    vi.stubGlobal("window", {location:{origin:"https://bank.example"}});
    const url="/oauth/consent"+search;
    fetchMock.mockResolvedValue(new Response(JSON.stringify({authorize_url:url})));
    expect(await startOAuth(new AbortController().signal)).toBe(url);
    for(const authorize_url of ["https://bank.example"+url,"//bank.example"+url,"/oauth/consent/"+search,"/oauth/consent?state=s",url+"#x",url+"&state=x",url.replace("consent","%63onsent"),url+" "]) {
      expect(() => validateOAuthStart({authorize_url})).toThrow(ApiError);
    }
    expect(() => validateOAuthStart({authorize_url:url, access_token:"x"})).toThrow(ApiError);
  });
  it.each([{}, {required:true}, {required:"false",ready:true}, {required:false,ready:true,token:"x"}])("rejects malformed status", async response => {
    const {getOAuthStatus}=await import("./oauthClient");
    fetchMock.mockResolvedValue(new Response(JSON.stringify(response)));
    await expect(getOAuthStatus(new AbortController().signal)).rejects.toMatchObject({code:"SERVICE_UNAVAILABLE"});
    expect(fetchMock).toHaveBeenCalledOnce();
  });
  it.each([{ready:false}, {ready:true,access_token:"x"}, {}, null])("never retries invalid callback responses", async response => {
    const {completeOAuth}=await import("./oauthClient");
    fetchMock.mockResolvedValue(new Response(JSON.stringify(response)));
    await expect(completeOAuth({state:"s",code:"c"},new AbortController().signal)).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledOnce();
  });
  it("fails closed on 503 unknown mode without replay", async () => {
    const {getOAuthStatus}=await import("./oauthClient");
    fetchMock.mockResolvedValue(new Response("private",{status:503}));
    await expect(getOAuthStatus(new AbortController().signal)).rejects.toBeInstanceOf(ApiError);
    expect(fetchMock).toHaveBeenCalledOnce();
  });
  it("rejects absent, pre-aborted and changed sessions", async () => {
    const {getOAuthStatus}=await import("./oauthClient");
    token=null;
    await expect(getOAuthStatus(new AbortController().signal)).rejects.toMatchObject({code:"AUTH_REQUIRED"});
    token="jwt"; const aborted=new AbortController(); aborted.abort();
    await expect(getOAuthStatus(aborted.signal)).rejects.toBeDefined();
    expect(fetchMock).not.toHaveBeenCalled();
    fetchMock.mockImplementation(async () => {token="changed";return new Response(JSON.stringify({required:true,ready:true}));});
    await expect(getOAuthStatus(new AbortController().signal)).rejects.toMatchObject({code:"AUTH_REQUIRED"});
  });
});
