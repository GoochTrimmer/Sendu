"use server";

import { revalidatePath } from "next/cache";
import { redirect } from "next/navigation";
import { requireUser } from "@/lib/auth";
import { parseTerms } from "@/lib/format";
import { CURRENCIES, MARKETPLACES, PRIORITIES, type Priority } from "@/lib/types";

export type SearchFormState = { error?: string };

function num(v: FormDataEntryValue | null): number | null {
  const s = String(v ?? "").trim();
  if (!s) return null;
  const n = Number(s);
  return Number.isFinite(n) && n >= 0 ? n : null;
}

export async function saveSearch(
  searchId: string | null,
  _prev: SearchFormState,
  formData: FormData,
): Promise<SearchFormState> {
  const { supabase, userId } = await requireUser();

  const title = String(formData.get("title") ?? "").trim();
  const query = String(formData.get("query") ?? "").trim();
  const codes = MARKETPLACES.map((m) => m.code as string);
  const marketplaces = formData.getAll("marketplaces").map(String).filter((c) => codes.includes(c));
  if (!query) return { error: "Query is required." };
  if (!marketplaces.length) return { error: "Pick at least one marketplace." };

  const priority = String(formData.get("priority")) as Priority;
  const currency = String(formData.get("currency") ?? "SGD");
  const row = {
    user_id: userId,
    title: title || query,
    query,
    group_name: String(formData.get("group_name") ?? "").trim() || null,
    marketplaces,
    include_terms: parseTerms(formData.get("include_terms")),
    exclude_terms: parseTerms(formData.get("exclude_terms")),
    exclude_enabled: formData.get("exclude_enabled") === "on",
    spam_filter_enabled: formData.get("spam_filter_enabled") === "on",
    min_price: num(formData.get("min_price")),
    max_price: num(formData.get("max_price")),
    currency: (CURRENCIES as readonly string[]).includes(currency) ? currency : "SGD",
    sizes: parseTerms(formData.get("sizes")),
    priority: PRIORITIES.includes(priority) ? priority : "normal",
  };

  let id = searchId;
  if (id) {
    const { error } = await supabase.from("saved_searches").update(row).eq("id", id);
    if (error) return { error: error.message };
  } else {
    const { data, error } = await supabase.from("saved_searches").insert(row).select("id").single();
    if (error) return { error: error.message };
    id = data.id as string;
  }

  // Per-marketplace overrides: keep rows only for selected marketplaces with something set.
  const upserts = [];
  const removals = [];
  for (const mp of codes) {
    const q = String(formData.get(`override_query_${mp}`) ?? "").trim();
    const ex = String(formData.get(`override_exclude_${mp}`) ?? "inherit");
    const extra = parseTerms(formData.get(`override_extra_${mp}`));
    const hasValue = q || ex !== "inherit" || extra.length;
    if (marketplaces.includes(mp) && hasValue) {
      upserts.push({
        search_id: id,
        marketplace: mp,
        query_override: q || null,
        exclude_enabled: ex === "inherit" ? null : ex === "on",
        extra_exclude_terms: extra,
      });
    } else {
      removals.push(mp);
    }
  }
  if (upserts.length) {
    const { error } = await supabase.from("search_marketplace_overrides").upsert(upserts);
    if (error) return { error: error.message };
  }
  if (removals.length) {
    await supabase.from("search_marketplace_overrides").delete().eq("search_id", id).in("marketplace", removals);
  }

  revalidatePath("/searches");
  redirect("/searches");
}

export async function togglePause(id: string, paused: boolean) {
  const { supabase } = await requireUser();
  await supabase.from("saved_searches").update({ is_paused: !paused }).eq("id", id);
  revalidatePath("/searches");
}

export async function deleteSearch(id: string) {
  const { supabase } = await requireUser();
  await supabase.from("saved_searches").delete().eq("id", id);
  revalidatePath("/searches");
  redirect("/searches");
}

export async function duplicateSearch(id: string) {
  const { supabase, userId } = await requireUser();
  const { data: s } = await supabase
    .from("saved_searches")
    .select("*,search_marketplace_overrides(*)")
    .eq("id", id)
    .single();
  if (!s) return;
  const overrides = s.search_marketplace_overrides as Record<string, unknown>[] | null;
  const rest: Record<string, unknown> = { ...s };
  for (const k of ["id", "created_at", "updated_at", "search_marketplace_overrides"]) delete rest[k];
  const { data: copy, error } = await supabase
    .from("saved_searches")
    .insert({ ...rest, user_id: userId, title: `${s.title} (copy)`, is_paused: true })
    .select("id")
    .single();
  if (error || !copy) return;
  if (overrides?.length) {
    await supabase.from("search_marketplace_overrides").insert(
      overrides.map((o) => ({ ...o, search_id: copy.id })),
    );
  }
  revalidatePath("/searches");
  redirect(`/searches/${copy.id}`);
}
