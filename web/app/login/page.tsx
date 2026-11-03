"use client";

import { useActionState, useState } from "react";
import { sendPasswordLink, signInWithPassword, type LoginState } from "./actions";

const IDLE: LoginState = { status: "idle" };

export default function LoginPage() {
  const [mode, setMode] = useState<"password" | "link">("password");
  const [email, setEmail] = useState("");
  const [signInState, signInAction, signingIn] = useActionState(signInWithPassword, IDLE);
  const [linkState, linkAction, sending] = useActionState(sendPasswordLink, IDLE);

  const emailField = (
    <input
      name="email"
      type="email"
      required
      autoComplete="email"
      placeholder="you@example.com"
      className="input"
      value={email}
      onChange={(e) => setEmail(e.target.value)}
    />
  );

  return (
    <div className="mx-auto mt-16 max-w-sm">
      <h1 className="text-2xl font-bold tracking-tight">
        {mode === "password" ? "Sign in" : "Set or reset password"}
      </h1>
      <p className="mt-1 text-sm text-muted">
        {mode === "password" ? "Invite only." : "We’ll email you a link to set a new password."}
      </p>

      {mode === "password" ? (
        <form action={signInAction} className="mt-6 space-y-3">
          {emailField}
          <input
            name="password"
            type="password"
            required
            autoComplete="current-password"
            placeholder="Password"
            className="input"
          />
          <button className="btn-primary w-full" disabled={signingIn}>
            {signingIn ? "Signing in…" : "Sign in"}
          </button>
          {signInState.status === "error" && (
            <p className="text-sm text-danger">{signInState.message}</p>
          )}
        </form>
      ) : linkState.status === "sent" ? (
        <div className="card mt-6 p-4 text-sm">{linkState.message}</div>
      ) : (
        <form action={linkAction} className="mt-6 space-y-3">
          {emailField}
          <button className="btn-primary w-full" disabled={sending}>
            {sending ? "Sending…" : "Email me a link"}
          </button>
          {linkState.status === "error" && (
            <p className="text-sm text-danger">{linkState.message}</p>
          )}
        </form>
      )}

      <button
        type="button"
        onClick={() => setMode(mode === "password" ? "link" : "password")}
        className="mt-4 text-sm text-muted underline-offset-2 hover:text-text hover:underline"
      >
        {mode === "password" ? "No password yet, or forgot it?" : "← Back to sign in"}
      </button>
    </div>
  );
}
