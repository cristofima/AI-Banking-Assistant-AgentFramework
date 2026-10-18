import { Children, isValidElement, type ReactNode } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { AppRoutes, RequireRole } from "./App";
import { UiLocaleProvider } from "./context/UiLocaleProvider";
import type { AuthenticatedUser } from "./api/authClient";

const auth = vi.hoisted(() => ({ user: null as AuthenticatedUser | null, loading: false, sessionKey: 1, logout: vi.fn() }));
const mounts = vi.hoisted(() => ({ customer: vi.fn() }));
vi.mock("./context/AuthContext", () => ({ useAuth: () => auth }));
vi.mock("./context/AgentResponseContext", () => ({ AgentResponseProvider: ({ children }: { children: ReactNode }) => { mounts.customer(); return <>{"CUSTOMER_CHAT"}{children}</>; } }));
vi.mock("./components/Navigation", () => ({ default: () => { mounts.customer(); return "CUSTOMER_NAVIGATION"; } }));
vi.mock("./components/Sidebar", () => ({ default: () => { mounts.customer(); return "CUSTOMER_SIDEBAR"; } }));
vi.mock("./components/AIAgent", () => ({ default: () => { mounts.customer(); return "CUSTOMER_AGENT"; } }));
vi.mock("./pages/Dashboard", () => ({ default: () => { mounts.customer(); return "CUSTOMER_DASHBOARD"; } }));
vi.mock("./pages/Account", () => ({ default: () => { mounts.customer(); return "CUSTOMER_ACCOUNT"; } }));
vi.mock("./pages/ProductDetail", () => ({ default: () => { mounts.customer(); return "CUSTOMER_ANALYTICS"; } }));
vi.mock("./pages/Support", () => ({ default: () => { mounts.customer(); return "CUSTOMER_SUPPORT"; } }));
vi.mock("./pages/SupportCases", () => ({ default: () => { mounts.customer(); return "CUSTOMER_CASES"; } }));
vi.mock("./pages/SupportCaseDetail", () => ({ default: () => { mounts.customer(); return "CUSTOMER_CASE"; } }));

const staff = (role: "admin" | "operator", locale = "en"): AuthenticatedUser => ({ id: role, email: `${role}@example.test`, role, locale, name: null, identityVersion: 1 });
const renderRoute = (path: string) => renderToStaticMarkup(
  <QueryClientProvider client={new QueryClient()}>
    <MemoryRouter initialEntries={[path]}><UiLocaleProvider><AppRoutes /></UiLocaleProvider></MemoryRouter>
  </QueryClientProvider>,
);

describe("role isolated routing", () => {
  it.each(["admin", "operator", "anonymous", "restoring"] as const)("keeps OAuth consent absent for %s", (role) => {
    auth.user = role === "admin" || role === "operator" ? staff(role) : null;
    auth.loading = role === "restoring";
    try {
      expect(renderRoute("/oauth/consent")).not.toContain("Authorize banking read access");
    } finally { auth.loading = false; }
  });
  it.each(["admin", "operator", "anonymous", "restoring"] as const)("keeps OAuth callback controls absent for %s", (role) => {
    auth.user = role === "admin" || role === "operator" ? staff(role) : null;
    auth.loading = role === "restoring";
    try { expect(renderRoute("/oauth/callback?state=s&code=c")).not.toContain("Finish authorization"); }
    finally { auth.loading = false; }
  });
  it("mounts standalone customer callback without chat", () => {
    auth.user = { ...staff("admin"), role: "customer", customerId: "customer" };
    mounts.customer.mockClear();
    expect(renderRoute("/oauth/callback?state=s&code=c")).toContain("Finish authorization");
    expect(mounts.customer).not.toHaveBeenCalled();
  });
  it("mounts standalone customer consent without chat", () => {
    auth.user = { ...staff("admin"), role: "customer", customerId: "customer" };
    mounts.customer.mockClear();
    const html = renderRoute("/oauth/consent");
    expect(html).toContain("Invalid authorization request");
    expect(html).not.toContain("CUSTOMER_");
    expect(mounts.customer).not.toHaveBeenCalled();
  });
  it("changes the shell identity for logout/login, user, role and identity-version transitions", () => {
    const customer: AuthenticatedUser = { id: "customer-user", email: "customer@example.test", role: "customer", customerId: "customer", locale: "en", name: null, identityVersion: 1 };
    const keys: Array<string | null> = [];
    const Probe = () => {
      const shell = RequireRole({ children: "session-local-chat", role: auth.user!.role });
      keys.push(shell.key);
      return shell;
    };
    for (const [user, sessionKey] of [
      [customer, 1], [customer, 2], [{ ...customer, id: "other-user", customerId: "other-customer" }, 2],
      [staff("operator"), 2], [{ ...staff("operator"), identityVersion: 2 }, 2],
    ] as const) {
      auth.user = user;
      auth.sessionKey = sessionKey;
      auth.loading = false;
      renderToStaticMarkup(<MemoryRouter><UiLocaleProvider><Probe /></UiLocaleProvider></MemoryRouter>);
    }
    expect(keys).toEqual(["1:customer-user:customer:1", "2:customer-user:customer:1", "2:other-user:customer:1", "2:operator:operator:1", "2:operator:operator:2"]);
    expect(new Set(keys).size).toBe(keys.length);
  });
  it.each(["/", "/account", "/product/owned-id", "/support", "/support-cases", "/support-cases/case-id", "/unknown"])("mounts customer chat on %s", (path) => {
    auth.user = { ...staff("admin"), id: "customer-user", role: "customer", customerId: "customer" };
    auth.loading = false;
    const html = renderRoute(path);
    expect(html).toContain("CUSTOMER_CHAT");
    expect(html).toContain("CUSTOMER_AGENT");
  });
  it.each(["restoring", "anonymous", "failed-profile"])("keeps customer chat absent when %s", (state) => {
    auth.user = state === "restoring" ? { ...staff("admin"), role: "customer", customerId: "customer" } : null;
    auth.loading = state === "restoring";
    mounts.customer.mockClear();
    try {
      expect(renderRoute("/support-cases/case-id")).not.toContain("CUSTOMER_");
      expect(mounts.customer).not.toHaveBeenCalled();
    } finally {
      auth.loading = false;
    }
  });
  it.each(["credit-cards", "portfolio"])("redirects the removed %s page to the catalog", (path) => {
    const customerGroup = Children.toArray(AppRoutes().props.children).find(
      (node) => isValidElement<{ element?: ReactNode }>(node)
        && isValidElement<{ role?: string }>(node.props.element)
        && node.props.element.props.role === "customer",
    );
    if (!isValidElement<{ children?: ReactNode }>(customerGroup)) throw new Error("Customer routes missing");
    const route = Children.toArray(customerGroup.props.children).find(
      (node) => isValidElement<{ path?: string }>(node) && node.props.path === path,
    );
    if (!isValidElement<{ element?: ReactNode }>(route)) throw new Error("Legacy route missing");
    expect(isValidElement(route.props.element) && route.props.element.type).toBe(Navigate);
    if (!isValidElement<{ to: string; replace: boolean }>(route.props.element)) throw new Error("Redirect missing");
    expect(route.props.element.props.to).toBe("/");
    expect(route.props.element.props.replace).toBe(true);
  });
  it.each(["admin", "operator"] as const)("never mounts customer functionality for %s deep links", (role) => {
    auth.user = staff(role);
    mounts.customer.mockClear();
    for (const path of ["/", "/account", "/analytics", "/product/opaque-id", "/credit-cards", "/portfolio", "/support", "/support-cases", "/support-cases/case-id", "/unknown"]) {
      expect(renderRoute(path)).not.toContain("CUSTOMER_");
    }
    expect(mounts.customer).not.toHaveBeenCalled();
  });
  it("admin has identity management only", () => {
    auth.user = staff("admin", "es");
    mounts.customer.mockClear();
    const html = renderRoute("/admin/operators");
    expect(html).toContain("Administración de identidades");
    expect(html).toContain("Crear operador");
    expect(html).not.toContain("CUSTOMER_");
    expect(mounts.customer).not.toHaveBeenCalled();
  });
  it("admin creation deep link reuses the localized form and contrasting navigation", () => {
    auth.user = staff("admin", "es");
    const html = renderRoute("/admin/operators/create");
    expect(html).toContain('aria-label="Crear operador"');
    expect(html).toContain('id="operator-password"');
    expect(html).not.toContain("Cargando operadores");
    expect(html).not.toContain("<table");
    expect(html).toMatch(/<a[^>]*class="[^"]*bg-primary[^"]*"[^>]*href="\/admin\/operators"/);
    expect(html).toMatch(/<a[^>]*class="[^"]*border[^"]*bg-card[^"]*"[^>]*href="\/admin\/customers"/);
  });
  it.each(["customer", "operator", "anonymous", "restoring"] as const)("%s cannot mount the operator creation page", (role) => {
    auth.user = role === "customer" ? { ...staff("admin"), role, customerId: "customer" } : role === "operator" ? staff(role) : null;
    auth.loading = role === "restoring";
    try {
      expect(renderRoute("/admin/operators/create")).not.toContain('id="operator-password"');
    } finally {
      auth.loading = false;
    }
  });
  it.each(["index", "*"])("redirects the operator %s route to the canonical queue", (path) => {
    const group = Children.toArray(AppRoutes().props.children).find(
      (node) => isValidElement<{ path?: string }>(node) && node.props.path === "/operator",
    );
    if (!isValidElement<{ children?: ReactNode }>(group)) throw new Error("Operator routes missing");
    const route = Children.toArray(group.props.children).find(
      (node) => isValidElement<{ index?: boolean; path?: string }>(node)
        && (path === "index" ? node.props.index : node.props.path === path),
    );
    if (!isValidElement<{ element?: ReactNode }>(route)) throw new Error("Operator redirect missing");
    if (!isValidElement<{ to: string; replace: boolean }>(route.props.element)) throw new Error("Redirect missing");
    expect(route.props.element.type).toBe(Navigate);
    expect(route.props.element.props.to).toBe(path === "index" ? "support-cases" : "/operator/support-cases");
    expect(route.props.element.props.replace).toBe(true);
  });
  it("operator shell exposes the review queue in Portuguese without customer chat", () => {
    auth.user = staff("operator", "pt");
    const html = renderRoute("/operator/support-cases");
    expect(html).toContain("Fila de revisão de contestações");
    expect(html).not.toContain("CUSTOMER_");
    expect(html).not.toContain("Create operator");
  });
  it("admin customer management stays isolated and localized", () => {
    auth.user = staff("admin", "es");
    mounts.customer.mockClear();
    const html = renderRoute("/admin/customers");
    expect(html).toContain("Usuarios clientes");
    expect(html).toContain('href="/admin/operators"');
    expect(html).toContain('href="/admin/customers"');
    expect(html).not.toContain("CUSTOMER_");
    expect(mounts.customer).not.toHaveBeenCalled();
  });
  it.each(["customer", "operator"] as const)("%s cannot mount customer administration", (role) => {
    auth.user = role === "customer" ? { ...staff("admin"), role, customerId: "customer" } : staff(role);
    expect(renderRoute("/admin/customers")).not.toContain("Manage existing customer sign-in access only.");
    expect(renderRoute("/operator")).not.toContain('href="/admin/customers"');
  });
  it("operator cannot mount admin management", () => {
    auth.user = staff("operator");
    expect(renderRoute("/admin/operators")).not.toContain("Create operator");
  });
  it("customer cannot mount either staff shell", () => {
    auth.user = { ...staff("admin"), role: "customer", customerId: "customer" };
    expect(renderRoute("/admin/operators")).not.toContain("Create operator");
    expect(renderRoute("/operator")).not.toContain("Dispute review queue");
  });
  it("does not mount a protected child while unauthenticated or restoring", () => {
    auth.user = null;
    expect(renderToStaticMarkup(<MemoryRouter><RequireRole role="admin"><span>SECRET_CHILD</span></RequireRole></MemoryRouter>)).not.toContain("SECRET_CHILD");
    auth.loading = true;
    expect(renderToStaticMarkup(<MemoryRouter><RequireRole role="admin"><span>SECRET_CHILD</span></RequireRole></MemoryRouter>)).not.toContain("SECRET_CHILD");
    auth.loading = false;
  });
});
