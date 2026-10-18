import type { ReactNode } from "react";
import {
  BrowserRouter,
  Navigate,
  Outlet,
  Route,
  Routes,
  useLocation,
} from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Toaster } from "@/components/ui/toaster";
import { Toaster as Sonner } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import Sidebar from "./components/Sidebar";
import Navigation from "./components/Navigation";
import AIAgent from "./components/AIAgent";
import Dashboard from "./pages/Dashboard";
import ProductDetail from "./pages/ProductDetail";
import Account from "./pages/Account";
import Support from "./pages/Support";
import SupportCases from "./pages/SupportCases";
import SupportCaseDetail from "./pages/SupportCaseDetail";
import NotFound from "./pages/NotFound";
import Login from "./pages/Login";
import OAuthConsent from "./pages/OAuthConsent";
import OAuthCallback from "./pages/OAuthCallback";
import { OAuthReadinessProvider } from "@/context/OAuthReadinessContext";
import { AuthProvider, useAuth } from "@/context/AuthContext";
import { AgentResponseProvider } from "@/context/AgentResponseContext";
import { UiLocaleProvider } from "@/context/UiLocaleProvider";
import { useTranslation } from "react-i18next";
import type { UserRole } from "@/api/authClient";
import { roleHome } from "@/api/roleRoutes";
import StaffShell from "@/components/StaffShell";
import OperatorCases from "@/pages/OperatorCases";
import OperatorManagement, { OperatorCreate } from "@/pages/OperatorManagement";
import CustomerUserManagement from "@/pages/CustomerUserManagement";

const queryClient = new QueryClient();

const ProtectedShell = () => (
  <OAuthReadinessProvider>
  <AgentResponseProvider>
    <div className="min-h-screen bg-background flex flex-col w-full">
      <Navigation />
      <div className="flex flex-1">
        <Sidebar />
        <main className="flex-1 overflow-auto">
          <Outlet />
        </main>
      </div>
      <AIAgent />
    </div>
  </AgentResponseProvider>
  </OAuthReadinessProvider>
);

export const RequireRole = ({ children, role }: { children: ReactNode; role: UserRole }) => {
  const { t } = useTranslation();
  const { user, loading, sessionKey } = useAuth();
  const location = useLocation();

  if (loading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <p className="text-base text-slate-500">
          {t("Restoring your workspace...")}
        </p>
      </div>
    );
  }

  if (!user) {
    return <Navigate to="/login" state={{ from: location }} replace />;
  }

  if (user.role !== role) return <Navigate to={roleHome(user.role)} replace />;
  return <div key={`${sessionKey}:${user.id}:${user.role}:${user.identityVersion}`}>{children}</div>;
};

const App = () => (
  <QueryClientProvider client={queryClient}>
    <TooltipProvider>
      <Toaster />
      <Sonner />
      <BrowserRouter>
        <AuthProvider>
          <UiLocaleProvider>
            <AppRoutes />
          </UiLocaleProvider>
        </AuthProvider>
      </BrowserRouter>
    </TooltipProvider>
  </QueryClientProvider>
);

export const AppRoutes = () => (
  <Routes>
    <Route path="/login" element={<Login />} />
    <Route path="/admin" element={<RequireRole role="admin"><StaffShell /></RequireRole>}>
      <Route index element={<Navigate to="operators" replace />} />
      <Route path="operators" element={<OperatorManagement />} />
      <Route path="operators/create" element={<OperatorCreate />} />
      <Route path="customers" element={<CustomerUserManagement />} />
      <Route path="*" element={<Navigate to="/admin/operators" replace />} />
    </Route>
    <Route path="/operator" element={<RequireRole role="operator"><StaffShell /></RequireRole>}>
      <Route index element={<Navigate to="support-cases" replace />} />
      <Route path="support-cases" element={<OperatorCases />} />
      <Route path="support-cases/:caseId" element={<OperatorCases />} />
      <Route path="*" element={<Navigate to="/operator/support-cases" replace />} />
    </Route>
    <Route
      element={
        <RequireRole role="customer">
          <ProtectedShell />
        </RequireRole>
      }
    >
      <Route index element={<Dashboard />} />
      <Route path="credit-cards" element={<Navigate to="/" replace />} />
      <Route path="portfolio" element={<Navigate to="/" replace />} />
      <Route path="product/:productId" element={<ProductDetail />} />
      <Route path="analytics" element={<Navigate to="/" replace />} />
      <Route path="account" element={<Account />} />
      <Route path="support" element={<Support />} />
      <Route path="support-cases" element={<SupportCases />} />
      <Route
        path="support-cases/:caseId"
        element={<SupportCaseDetail />}
      />
      <Route path="*" element={<NotFound />} />
    </Route>
    <Route path="/oauth/consent" element={<RequireRole role="customer"><OAuthConsent /></RequireRole>} />
    <Route path="/oauth/callback" element={<OAuthCallback />} />
  </Routes>
);

export default App;
