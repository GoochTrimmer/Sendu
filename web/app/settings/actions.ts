"use server";

import { randomBytes } from "node:crypto";
import { revalidatePath } from "next/cache";
import { requireUser } from "@/lib/auth";

export async function saveSettings(formData: FormData) {
  const { supabase, userId } = await requireUser();
  const mode = String(formData.get("notify_mode"));
  await supabase
    .from("profiles")
    .update({
      display_name: String(formData.get("display_name") ?? "").trim() || null,
      notify_mode: ["instant", "digest", "off"].includes(mode) ? mode : "instant",
    })
    .eq("id", userId);
  revalidatePath("/settings");
}

/** One-time code the user sends to the bot as `/start <code>`. The worker links it on its next run. */
export async function generateLinkCode() {
  const { supabase, userId } = await requireUser();
  const code = randomBytes(6).toString("base64url").replace(/[-_]/g, "x").slice(0, 8).toUpperCase();
  await supabase.from("profiles").update({ telegram_link_code: code }).eq("id", userId);
  revalidatePath("/settings");
}
