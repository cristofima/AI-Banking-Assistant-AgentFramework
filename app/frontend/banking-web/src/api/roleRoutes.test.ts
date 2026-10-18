import { describe, expect, it } from "vitest";
import { loginDestination, roleHome, savedLoginDestination } from "./roleRoutes";

const oauthSearch = "?" + new URLSearchParams({
  client_id: "registered-client", redirect_uri: "https://client.example/callback?tenant=demo",
  response_type: "code", scope: "mcp.access", state: "original-state",
  code_challenge: "a".repeat(43), code_challenge_method: "S256",
}).toString();

describe("OAuth login return", () => {
  it("preserves only the complete validated customer consent query", () => {
    const saved = savedLoginDestination({ from: { pathname: "/oauth/consent", search: oauthSearch, hash: "" } });
    expect(saved).toBe("/oauth/consent" + oauthSearch);
    expect(loginDestination("customer", saved)).toBe(saved);
    expect(loginDestination("admin", saved)).toBe("/admin/operators");
    expect(loginDestination("operator", saved)).toBe("/operator");
  });
  it.each([null, "bad", { from: null }, { from: { pathname: 123 } },
    { from: { pathname: "/oauth/consent" } },
    { from: { pathname: "/oauth/consent", search: oauthSearch + "&state=second" } },
    { from: { pathname: "/oauth/consent", search: oauthSearch, hash: "#fragment" } },
  ])("rejects malformed saved OAuth location", (state) => {
    expect(savedLoginDestination(state)).toBeUndefined();
  });
  it("retains existing pathname-only safety for all other routes", () => {
    expect(savedLoginDestination({ from: { pathname: "/account", search: "?extra=x", hash: "#x" } })).toBe("/account");
    expect(loginDestination("customer", "/oauth/consent")).toBe("/");
    expect(loginDestination("customer", "/oauth/consent/" + oauthSearch)).toBe("/");
    expect(loginDestination("customer", "/oauth/consent" + oauthSearch + "&next=https://evil.example")).toBe("/");
  });
});

describe("role safe destinations", () => {
  it.each(["/product/opaque-id", "/product/product%20id", "/", "/account", "/analytics", "/credit-cards", "/support", "/portfolio", "/support-cases", "/support-cases/case-id"])("preserves customer route %s", (path) => {
    expect(loginDestination("customer", path)).toBe(path);
    expect(loginDestination("admin", path)).toBe("/admin/operators");
    expect(loginDestination("operator", path)).toBe("/operator");
  });
  it.each(["//external.test", "https://external.test", "/admin/operators", "/operator", "/account?x=1", "/support-cases/id#x", "/support-cases/..", "/support-cases/%2e%2e", "/support-cases/id\\path", "/unknown", "/product/..", "/product/%2e%2e", "/product/%", "/product/%ff", "/product/id%2fpath", "/product/id%5cpath", "/product/id?x=1"])("rejects unsafe saved path %s", (path) => {
    expect(loginDestination("customer", path)).toBe("/");
  });
  it("rejects the former Spanish product route", () => {
    expect(loginDestination("customer", "/producto/opaque-id")).toBe("/");
  });
  it("uses a role destination without a saved route", () => {
    expect(roleHome("customer")).toBe("/");
    expect(loginDestination("admin")).toBe("/admin/operators");
    expect(loginDestination("operator")).toBe("/operator");
  });
});

it("never saves callback protocol data through login",()=>{
  expect(savedLoginDestination({from:{pathname:"/oauth/callback",search:"?state=s&code=c"}})).toBeUndefined();
  expect(loginDestination("customer","/oauth/callback?state=s&code=c")).toBe("/");
});
