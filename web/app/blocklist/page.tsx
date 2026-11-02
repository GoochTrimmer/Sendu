import Link from "next/link";
import { requireUser } from "@/lib/auth";
import { formatTime, marketplaceName } from "@/lib/format";
import type { BlockedSellerRow } from "@/lib/types";
import { removeBlocked } from "./actions";
import { AddBlockedForm } from "./AddBlockedForm";

export default async function BlocklistPage(props: PageProps<"/blocklist">) {
  const sp = await props.searchParams;
  const tab = sp.tab === "shared" ? "shared" : "personal";
  const { supabase, userId } = await requireUser();

  let q = supabase
    .from("blocked_sellers")
    .select("*,creator:profiles!blocked_sellers_created_by_fkey(display_name)")
    .eq("scope", tab)
    .order("created_at", { ascending: false });
  if (tab === "personal") q = q.eq("owner_id", userId);
  const { data, error } = await q;
  const rows = (data ?? []) as BlockedSellerRow[];

  const tabClass = (t: string) =>
    `rounded-md px-3 py-1.5 text-sm ${tab === t ? "bg-accent text-accent-text" : "text-muted hover:bg-surface-2"}`;

  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <h1 className="text-xl font-bold tracking-tight">Blocklist</h1>
      <div className="flex gap-1">
        <Link href="/blocklist" className={tabClass("personal")}>Personal</Link>
        <Link href="/blocklist?tab=shared" className={tabClass("shared")}>Shared</Link>
      </div>
      <p className="text-sm text-muted">
        {tab === "personal"
          ? "Sellers hidden from your feed and alerts only."
          : "Sellers hidden for everyone in the group. Use it for bots and mass resellers."}
      </p>

      <AddBlockedForm key={tab} scope={tab} />

      {error && <p className="text-sm text-danger">{error.message}</p>}
      <ul className="space-y-2">
        {rows.length === 0 && <li className="text-sm text-muted">No blocked sellers.</li>}
        {rows.map((b) => {
          const canRemove = b.owner_id === userId || (b.scope === "shared" && b.created_by === userId);
          return (
            <li key={b.id} className="card flex items-start justify-between gap-3 p-3">
              <div className="min-w-0 text-sm">
                <p className="font-medium">
                  {b.seller_name ?? b.seller_id.replace(/^name:/, "")}{" "}
                  <span className="chip ml-1">{marketplaceName(b.marketplace)}</span>
                </p>
                <p className="truncate text-xs text-muted">ID {b.seller_id}</p>
                <p className="text-xs text-muted">
                  {b.scope === "shared" && <>Added by {b.creator?.display_name ?? "someone"} · </>}
                  {formatTime(b.created_at)}
                  {b.reason && <> · {b.reason}</>}
                </p>
              </div>
              {canRemove && (
                <form action={removeBlocked.bind(null, b.id)}>
                  <button className="btn btn-sm">Remove</button>
                </form>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
