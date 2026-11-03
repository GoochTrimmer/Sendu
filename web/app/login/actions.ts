"use server";

import { redirect } from "next/navigation";
import { createClient } from "@/lib/supabase/server";

export type LoginState = { status: "idle" | "error"; message?: string };

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
        ? "Wrong email or password."
        : error.message,
    };
  }
  redirect("/");
}

export async function signOut() {
  const supabase = await createClient();
  await supabase.auth.signOut();
  redirect("/login");
}
