import Link from "next/link";
import { Suspense } from "react";
import { FilterBar } from "@/components/FilterBar";
import { ListingCard } from "@/components/ListingCard";
import { requireUser } from "@/lib/auth";
import type { BlockedSellerRow, MatchRow } from "@/lib/types";

const LIMIT = 150;

export default async function FeedPage(props: PageProps<"/">) {
  const sp = await props.searchParams;
  const one = (k: string) => (typeof sp[k] === "string" ? (sp[k] as string) : "");
  const { supabase, userId } = await requireUser();

  let q = supabase
    .from("matches")
    .select(
      "id,state,is_initial,matched_at,listing:listings!inner(*),search:saved_searches!inner(id,title,group_name)",
    )
    .eq("user_id", userId)
    .neq("state", "dismissed")
    .order("matched_at", { ascending: false })
    .limit(LIMIT);
  if (one("search")) q = q.eq("search_id", one("search"));
  if (one("group")) q = q.eq("search.group_name", one("group"));
  if (one("mp")) q = q.eq("listing.marketplace", one("mp"));
  if (one("initial") !== "show") q = q.eq("is_initial", false);

  const [{ data: matchData, error }, { data: searches }, { data: likes }, { data: blocked }] =
    await Promise.all([
      q,
      supabase.from("saved_searches").select("id,title,group_name").eq("user_id", userId).order("title"),
      supabase.from("listing_feedback").select("listing_id").eq("user_id", userId).eq("feedback", "like"),
      supabase.from("blocked_sellers").select("marketplace,seller_id,seller_name"),
    ]);

  const liked = new Set((likes ?? []).map((l) => l.listing_id as string));
  const blockedIds = new Set(
    ((blocked ?? []) as Pick<BlockedSellerRow, "marketplace" | "seller_id" | "seller_name">[]).map(
      (b) => `${b.marketplace}|${b.seller_id}`,
    ),
  );
  const isBlocked = (m: MatchRow) =>
    blockedIds.has(`${m.listing.marketplace}|${m.listing.seller_id}`) ||
    blockedIds.has(`${m.listing.marketplace}|name:${(m.listing.seller_name ?? "").toLowerCase()}`);

  // One card per listing, even if several searches matched it.
  const cards = new Map<string, { m: MatchRow; ids: string[]; titles: string[] }>();
  for (const m of (matchData ?? []) as unknown as MatchRow[]) {
    if (isBlocked(m)) continue;
    const c = cards.get(m.listing.id);
    if (c) {
      c.ids.push(m.id);
      if (!c.titles.includes(m.search.title)) c.titles.push(m.search.title);
    } else {
      cards.set(m.listing.id, { m, ids: [m.id], titles: [m.search.title] });
    }
  }
  const groups = [...new Set((searches ?? []).map((s) => s.group_name as string | null).filter(Boolean))] as string[];

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <h1 className="text-xl font-bold tracking-tight">Feed</h1>
        <Suspense>
          <FilterBar searches={(searches ?? []) as { id: string; title: string }[]} groups={groups} />
        </Suspense>
      </div>

      {error && <p className="text-sm text-danger">Couldn&apos;t load matches: {error.message}</p>}

      {cards.size === 0 ? (
        <EmptyState hasSearches={(searches ?? []).length > 0} />
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {[...cards.values()].map(({ m, ids, titles }) => (
            <ListingCard
              key={m.listing.id}
              listing={m.listing}
              liked={liked.has(m.listing.id)}
              matchIds={ids}
              searchTitles={titles}
              isInitial={m.is_initial}
              isNew={m.state === "new" && !m.is_initial}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function EmptyState({ hasSearches }: { hasSearches: boolean }) {
  return (
    <div className="card p-8 text-center text-sm text-muted">
      {hasSearches ? (
        <>
          Nothing new yet. New searches start with a silent first run; untick &ldquo;Hide initial
          results&rdquo; to see what they found.
        </>
      ) : (
        <>
          No saved searches yet.{" "}
          <Link href="/searches/new" className="font-medium text-text underline">
            Create your first search
          </Link>
          .
        </>
      )}
    </div>
  );
}
