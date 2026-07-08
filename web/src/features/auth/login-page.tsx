import { useState, type FormEvent } from "react";
import { useSearch } from "@tanstack/react-router";
import { LockKeyhole } from "lucide-react";

import { Button } from "@/components/ui/button";
import { login } from "@/shared/lib/api";

export function LoginPage() {
  const search = useSearch({ strict: false }) as { redirect?: string };
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const redirect = safeRedirect(search.redirect);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    setSubmitting(true);
    try {
      await login(password);
      window.location.assign(redirect);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Login failed");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-shell">
      <form className="login-panel" onSubmit={submit}>
        <div className="login-panel__mark">
          <LockKeyhole size={18} />
        </div>
        <div>
          <h1 className="login-panel__title">yuubot</h1>
          <p className="login-panel__sub">Admin access</p>
        </div>
        <label className="login-panel__field">
          <span>Password</span>
          <input
            className="input input--xl"
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoFocus
          />
        </label>
        {error && <div className="login-panel__error">{error}</div>}
        <Button type="submit" disabled={submitting || !password}>
          {submitting ? "Signing in" : "Sign in"}
        </Button>
      </form>
    </main>
  );
}

function safeRedirect(value: string | undefined): string {
  if (!value || !value.startsWith("/") || value.startsWith("//")) {
    return "/";
  }
  return value;
}
