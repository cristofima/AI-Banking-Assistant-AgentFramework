import { FormEvent, useState } from "react";
import { LoaderCircle, LockKeyhole } from "lucide-react";
import { Navigate, useLocation } from "react-router-dom";
import { loginDestination, savedLoginDestination } from "@/api/roleRoutes";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useAuth } from "@/context/AuthContext";
import { ApiError } from "@/api/errors";

const Login = () => {
  const { user, login } = useAuth();
  const location = useLocation();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  if (user) {
    return <Navigate to={loginDestination(user.role, savedLoginDestination(location.state))} replace />;
  }

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    const secret = password;
    setPassword("");
    try {
      await login(email, secret);
    } catch (loginError) {
      setError(
        loginError instanceof ApiError &&
          loginError.code === "INVALID_CREDENTIALS"
          ? "Invalid email or password"
          : "Sign in is unavailable",
      );
    } finally {
      setPassword("");
      setSubmitting(false);
    }
  };

  return (
    <div className="min-h-screen bg-slate-900 flex items-center justify-center px-4">
      <div className="w-full max-w-md rounded-lg bg-slate-950/80 border border-slate-800 p-10 shadow-2xl">
        <div className="space-y-3 text-center">
          <LockKeyhole
            className="mx-auto size-8 text-sky-400"
            aria-hidden="true"
          />
          <p className="text-lg font-semibold text-slate-200">
            Enterprise Banking
          </p>
          <h1 className="text-2xl font-bold text-white">Sign in</h1>
          <p className="text-sm text-slate-400">
            Use your banking assistant credentials to continue.
          </p>
        </div>

        <form className="mt-8 space-y-5" onSubmit={handleSubmit}>
          <div className="space-y-2">
            <Label htmlFor="email" className="text-slate-200">
              Email
            </Label>
            <Input
              id="email"
              type="email"
              autoComplete="username"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              required
              className="border-slate-700 bg-slate-900 text-white"
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="password" className="text-slate-200">
              Password
            </Label>
            <Input
              id="password"
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              required
              className="border-slate-700 bg-slate-900 text-white"
            />
          </div>
          {error && (
            <p role="alert" className="text-sm text-red-400">
              {error}
            </p>
          )}
          <Button type="submit" className="w-full" disabled={submitting}>
            {submitting && (
              <LoaderCircle
                className="mr-2 size-4 animate-spin"
                aria-hidden="true"
              />
            )}
            {submitting ? "Signing in" : "Sign in"}
          </Button>
        </form>

        <div className="mt-6 text-center text-sm text-slate-400">
          <p>
            Need help? Contact{" "}
            <a
              href="mailto:support@bankwise.com"
              className="text-sky-400 underline"
            >
              support
            </a>
            .
          </p>
        </div>
      </div>
    </div>
  );
};

export default Login;
