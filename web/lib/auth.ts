import "server-only";
import { redirect } from "next/navigation";
import { createClient } from "./supabase/server";

/** Returns the Supabase client and the signed-in user's id, or redirects to /login. */
export async function requireUser() {
  const supabase = await createClient();
  const { data } = await supabase.auth.getClaims();
  const userId = data?.claims?.sub;
  if (!userId) redirect("/login");
  return { supabase, userId };
}
