"use server";

import { revalidatePath } from "next/cache";
import { requireUser } from "@/lib/auth";
import { parseSellerInput } from "@/lib/format";

export type BlockFormState = { error?: string; ok?: string };

export async function addBlocked(_: BlockFormState, formData: FormData): Promise<BlockFormState> {
  const { supabase, userId } = await requireUser();
  const scope = formData.get("scope") === "shared" ? "shared" : "personal";
  const parsed = parseSellerInput(String(formData.get("seller") ?? ""), String(formData.get("marketplace") ?? ""));
  if (!parsed || !parsed.marketplace) {
    return { error: "Couldn't read that. Paste a seller profile URL, or pick a marketplace and paste the seller ID." };
  }
  const { error } = await supabase.from("blocked_sellers").insert({
    marketplace: parsed.marketplace,
    seller_id: parsed.sellerId,
    seller_name: parsed.sellerName,
    scope,
    owner_id: scope === "personal" ? userId : null,
    created_by: userId,
    reason: String(formData.get("reason") ?? "").trim() || null,
  });
  if (error) return { error: error.code === "23505" ? "That seller is already on this list." : error.message };
  revalidatePath("/blocklist");
  revalidatePath("/");
  return { ok: `Blocked ${parsed.sellerName ?? parsed.sellerId}.` };
}

export async function removeBlocked(id: string) {
  const { supabase } = await requireUser();
  await supabase.from("blocked_sellers").delete().eq("id", id);
  revalidatePath("/blocklist");
  revalidatePath("/");
}
