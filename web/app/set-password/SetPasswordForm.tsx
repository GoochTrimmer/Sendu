"use client";

import { useActionState } from "react";
import { setPassword, type SetPasswordState } from "./actions";

export function SetPasswordForm({ email }: { email?: string }) {
  const [state, action, pending] = useActionState<SetPasswordState, FormData>(setPassword, {
    status: "idle",
  });

  return (
    <form action={action} className="mt-6 space-y-3">
      {/* Lets password managers save the new password against the right account. */}
      <input
        type="email"
        name="username"
        autoComplete="username"
        defaultValue={email}
        hidden
        readOnly
      />
      <input
        name="password"
        type="password"
        required
        minLength={8}
        autoComplete="new-password"
        placeholder="New password (8+ characters)"
        className="input"
      />
      <input
        name="confirm"
        type="password"
        required
        minLength={8}
        autoComplete="new-password"
        placeholder="Confirm password"
        className="input"
      />
      <button className="btn-primary w-full" disabled={pending}>
        {pending ? "Saving…" : "Save password"}
      </button>
      {state.status === "error" && <p className="text-sm text-danger">{state.message}</p>}
    </form>
  );
}
