import { NextResponse, type NextRequest } from "next/server";
import { createClient } from "@/lib/supabase/server";

/** Opening a card marks the match as seen, then sends the user to the marketplace. */
export async function GET(request: NextRequest, ctx: RouteContext<"/go/[id]">) {
  const { id } = await ctx.params;
  const supabase = await createClient();
  const { data } = await supabase
    .from("matches")
    .select("id,state,listing:listings(url)")
    .eq("id", id)
    .maybeSingle();
  const listing = data?.listing as unknown as { url: string } | null;
  if (!data || !listing?.url) return NextResponse.redirect(new URL("/", request.nextUrl.origin));
  if (data.state === "new") {
    await supabase.from("matches").update({ state: "seen" }).eq("id", id);
  }
  return NextResponse.redirect(listing.url);
}
