"use server";

import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";

export type LoginState = { status: "idle" | "sent" | "error"; message?: string };

async function siteOrigin(): Promise<string> {
  const h = await headers();
  return h.get("origin") ?? `${h.get("x-forwarded-proto") ?? "https"}://${h.get("host")}`;
}

export async function signInWithPassword(_: LoginState, formData: FormData): Promise<LoginState> {
  const email = String(formData.get("email") ?? "").trim();
  const password = String(formData.get("password") ?? "");
  if (!email || !password) return { status: "error", message: "Enter your email and password." };

  const supabase = await createClient();
  const { error } = await supabase.auth.signInWithPassword({ email, password });
  if (error) {
    return {
      status: "error",
      message: /invalid login credentials/i.test(error.message)
        ? "Wrong email or password. No password yet? Use “No password yet, or forgot it?” below."
        : error.message,
    };
  }
  redirect("/");
}

/** "No password yet / forgot it": existing accounts get a link to set or change their password. */
export async function sendPasswordLink(_: LoginState, formData: FormData): Promise<LoginState> {
  const email = String(formData.get("email") ?? "").trim();
  if (!email) return { status: "error", message: "Enter your email." };

  const supabase = await createClient();
  const { data: exists, error: lookupError } = await supabase.rpc("email_registered", {
    p_email: email,
  });
  if (lookupError) return { status: "error", message: lookupError.message };
  if (!exists) return { status: "error", message: "This email hasn't been invited yet." };

  const { error } = await supabase.auth.resetPasswordForEmail(email, {
    redirectTo: `${await siteOrigin()}/auth/callback?next=/set-password`,
  });
  if (error) {
    return {
      status: "error",
      message: /rate limit/i.test(error.message)
        ? "Too many emails sent recently. Wait a few minutes and try again."
        : error.message,
    };
  }
  return {
    status: "sent",
    message:
      "This email already has an account. We’ve emailed you a link to set or change your password.",
  };
}

export async function signOut() {
  const supabase = await createClient();
  await supabase.auth.signOut();
  redirect("/login");
}
