"use server";

import { redirect } from "next/navigation";
import { requireUser } from "@/lib/auth";

export type SetPasswordState = { status: "idle" | "error"; message?: string };

export async function setPassword(
  _: SetPasswordState,
  formData: FormData,
): Promise<SetPasswordState> {
  const password = String(formData.get("password") ?? "");
  const confirm = String(formData.get("confirm") ?? "");
  if (password.length < 8) return { status: "error", message: "Use at least 8 characters." };
  if (password !== confirm) return { status: "error", message: "The passwords don’t match." };

  const { supabase } = await requireUser();
  const { error } = await supabase.auth.updateUser({ password });
  if (error) {
    return {
      status: "error",
      message: /different from the old/i.test(error.message)
        ? "That’s your current password. Choose a new one."
        : error.message,
    };
  }
  redirect("/");
}
