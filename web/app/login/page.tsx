"use client";

import { useActionState } from "react";
import { sendMagicLink, type LoginState } from "./actions";

export default function LoginPage() {
  const [state, action, pending] = useActionState<LoginState, FormData>(sendMagicLink, {
    status: "idle",
  });

  return (
    <div className="mx-auto mt-16 max-w-sm">
      <h1 className="text-2xl font-bold tracking-tight">Sign in</h1>
      <p className="mt-1 text-sm text-muted">Invite only. We&apos;ll email you a sign-in link.</p>
      {state.status === "sent" ? (
        <div className="card mt-6 p-4 text-sm">
          Check your inbox for the sign-in link. You can close this tab.
        </div>
      ) : (
        <form action={action} className="mt-6 space-y-3">
          <input
            name="email"
            type="email"
            required
            autoComplete="email"
            placeholder="you@example.com"
            className="input"
          />
          <button className="btn-primary w-full" disabled={pending}>
            {pending ? "Sending…" : "Email me a link"}
          </button>
          {state.status === "error" && <p className="text-sm text-danger">{state.message}</p>}
        </form>
      )}
    </div>
  );
}
