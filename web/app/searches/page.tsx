import Link from "next/link";
import { requireUser } from "@/lib/auth";
import { marketplaceName, timeAgo } from "@/lib/format";
import type { SavedSearch, SearchStateRow } from "@/lib/types";
import { togglePause } from "./actions";

const FILTER_LABELS: Record<string, string> = {
  spam: "spam",
  blocked: "blocked sellers",
  excluded: "excluded",
  include: "missing include terms",
  price: "price",
  size: "size",
};

export default async function SearchesPage() {
  const { supabase, userId } = await requireUser();
  // eslint-disable-next-line react-hooks/purity -- server component, rendered per request
  const since = new Date(Date.now() - 7 * 24 * 3600 * 1000).toISOString();

  const [{ data: searches }, { data: states }, { data: recent }, { data: likes }] = await Promise.all([
    supabase.from("saved_searches").select("*").eq("user_id", userId).order("title"),
    supabase.from("search_state").select("*"),
    supabase.from("matches").select("search_id,listing_id").eq("user_id", userId).gte("matched_at", since).eq("is_initial", false),
    supabase.from("listing_feedback").select("listing_id").eq("user_id", userId).eq("feedback", "like"),
  ]);

  const liked = new Set((likes ?? []).map((l) => l.listing_id as string));
  const counts = new Map<string, { matches: number; likes: number }>();
  for (const m of recent ?? []) {
    const c = counts.get(m.search_id) ?? { matches: 0, likes: 0 };
    c.matches += 1;
    if (liked.has(m.listing_id)) c.likes += 1;
    counts.set(m.search_id, c);
  }
  const stateBySearch = new Map<string, SearchStateRow[]>();
  for (const s of (states ?? []) as SearchStateRow[]) {
    stateBySearch.set(s.search_id, [...(stateBySearch.get(s.search_id) ?? []), s]);
  }

  const groups = new Map<string, SavedSearch[]>();
  for (const s of (searches ?? []) as SavedSearch[]) {
    const g = s.group_name || "Ungrouped";
    groups.set(g, [...(groups.get(g) ?? []), s]);
  }
  const groupNames = [...groups.keys()].sort((a, b) =>
    a === "Ungrouped" ? 1 : b === "Ungrouped" ? -1 : a.localeCompare(b),
  );

  return (
    <div className="space-y-5">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold tracking-tight">Searches</h1>
        <Link href="/searches/new" className="btn-primary">+ New search</Link>
      </div>

      {groupNames.length === 0 && (
        <div className="card p-8 text-center text-sm text-muted">
          No searches yet. Try &ldquo;yohji pour homme&rdquo; with a Japanese override for Mercari JP.
        </div>
      )}

      {groupNames.map((g) => (
        <section key={g} className="space-y-2">
          <h2 className="text-xs font-semibold uppercase tracking-wide text-muted">{g}</h2>
          <ul className="space-y-2">
            {groups.get(g)!.map((s) => (
              <SearchRow key={s.id} s={s} states={stateBySearch.get(s.id) ?? []} counts={counts.get(s.id)} />
            ))}
          </ul>
        </section>
      ))}
    </div>
  );
}

function SearchRow({
  s,
  states,
  counts,
}: {
  s: SavedSearch;
  states: SearchStateRow[];
  counts?: { matches: number; likes: number };
}) {
  const lastPolled = states
    .map((x) => x.last_polled_at)
    .filter(Boolean)
    .sort()
    .at(-1);
  const hidden: Record<string, number> = {};
  for (const st of states) {
    for (const [k, v] of Object.entries(st.last_run_stats ?? {})) {
      if (k in FILTER_LABELS && typeof v === "number" && v > 0) hidden[k] = (hidden[k] ?? 0) + v;
    }
  }
  const errors = states.filter((x) => x.last_error);
  const pending = s.marketplaces.filter((mp) => !states.find((x) => x.marketplace === mp && x.initialized));

  return (
    <li className="card p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <Link href={`/searches/${s.id}`} className="font-semibold hover:underline">
            {s.title}
          </Link>
          {s.title !== s.query && <span className="ml-2 text-xs text-muted">“{s.query}”</span>}
          <div className="mt-1 flex flex-wrap gap-1">
            <span className={`chip ${s.is_paused ? "" : "text-good"}`}>{s.is_paused ? "paused" : "active"}</span>
            <span className="chip">{s.priority}</span>
            {s.marketplaces.map((mp) => (
              <span key={mp} className="chip">{marketplaceName(mp)}</span>
            ))}
            {!s.spam_filter_enabled && <span className="chip">spam filter off</span>}
          </div>
        </div>
        <div className="flex gap-1">
          <form action={togglePause.bind(null, s.id, s.is_paused)}>
            <button className="btn btn-sm">{s.is_paused ? "Resume" : "Pause"}</button>
          </form>
          <Link href={`/searches/${s.id}`} className="btn btn-sm">Edit</Link>
        </div>
      </div>
      <p className="mt-2 text-xs text-muted">
        Polled {timeAgo(lastPolled)} · {counts?.matches ?? 0} matches / {counts?.likes ?? 0} likes in 7 days
        {pending.length > 0 && ` · first run pending on ${pending.map(marketplaceName).join(", ")}`}
      </p>
      {Object.keys(hidden).length > 0 && (
        <p className="mt-0.5 text-xs text-muted">
          Last run hid {Object.entries(hidden).map(([k, v]) => `${v} ${FILTER_LABELS[k]}`).join(", ")}
        </p>
      )}
      {errors.map((e) => (
        <p key={e.marketplace} className="mt-0.5 text-xs text-danger">
          {marketplaceName(e.marketplace)}: {e.last_error}
        </p>
      ))}
    </li>
  );
}
