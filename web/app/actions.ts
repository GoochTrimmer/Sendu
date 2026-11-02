"use server";

import { revalidatePath } from "next/cache";
import { requireUser } from "@/lib/auth";

export async function toggleLike(listingId: string, liked: boolean) {
  const { supabase, userId } = await requireUser();
  if (liked) {
    await supabase.from("listing_feedback").delete().eq("user_id", userId).eq("listing_id", listingId);
  } else {
    await supabase
      .from("listing_feedback")
      .upsert({ user_id: userId, listing_id: listingId, feedback: "like" });
  }
  revalidatePath("/");
  revalidatePath("/liked");
}

export async function dismissMatches(matchIds: string[]) {
  const { supabase } = await requireUser();
  // RLS limits this to the user's own rows; only `state` is updatable.
  await supabase.from("matches").update({ state: "dismissed" }).in("id", matchIds);
  revalidatePath("/");
}

export async function blockSeller(formData: FormData) {
  const { supabase, userId } = await requireUser();
  const scope = formData.get("scope") === "shared" ? "shared" : "personal";
  const marketplace = String(formData.get("marketplace") ?? "");
  const sellerId = String(formData.get("seller_id") ?? "").trim();
  if (!marketplace || !sellerId) return;
  const { error } = await supabase.from("blocked_sellers").insert({
    marketplace,
    seller_id: sellerId,
    seller_name: String(formData.get("seller_name") ?? "") || null,
    scope,
    owner_id: scope === "personal" ? userId : null,
    created_by: userId,
    reason: String(formData.get("reason") ?? "").trim() || null,
  });
  if (error && error.code !== "23505") throw new Error(error.message); // 23505 = already blocked
  revalidatePath("/");
  revalidatePath("/blocklist");
}
