"use client";

import { useActionState } from "react";
import { signInWithPassword, type LoginState } from "./actions";

export default function LoginPage() {
  const [state, action, pending] = useActionState<LoginState, FormData>(signInWithPassword, {
    status: "idle",
  });

  return (
    <div className="mx-auto mt-16 max-w-sm">
      <h1 className="text-2xl font-bold tracking-tight">Sign in</h1>
      <p className="mt-1 text-sm text-muted">Invite only.</p>
      <form action={action} className="mt-6 space-y-3">
        <input
          name="email"
          type="email"
          required
          autoComplete="email"
          placeholder="you@example.com"
          className="input"
        />
        <input
          name="password"
          type="password"
          required
          autoComplete="current-password"
          placeholder="Password"
          className="input"
        />
        <button className="btn-primary w-full" disabled={pending}>
          {pending ? "Signing in…" : "Sign in"}
        </button>
        {state.status === "error" && <p className="text-sm text-danger">{state.message}</p>}
      </form>
    </div>
  );
}
