import "server-only";
import type { createClient } from "./supabase/server";

export type FxRates = Record<string, number>; // SGD per 1 unit of currency

/** Daily market rates kept fresh by the worker (fx_rates table). Empty if unavailable. */
export async function loadFxRates(supabase: Awaited<ReturnType<typeof createClient>>): Promise<FxRates> {
  const { data, error } = await supabase.from("fx_rates").select("currency, rate_to_sgd");
  if (error || !data) return {};
  return Object.fromEntries(data.map((r) => [r.currency as string, Number(r.rate_to_sgd)]));
}
