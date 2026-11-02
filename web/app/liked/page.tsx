import { ListingCard } from "@/components/ListingCard";
import { requireUser } from "@/lib/auth";
import type { Listing } from "@/lib/types";

export default async function LikedPage() {
  const { supabase, userId } = await requireUser();
  const { data, error } = await supabase
    .from("listing_feedback")
    .select("created_at,listing:listings!inner(*)")
    .eq("user_id", userId)
    .eq("feedback", "like")
    .order("created_at", { ascending: false });

  const rows = (data ?? []) as unknown as { listing: Listing }[];
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-bold tracking-tight">Liked</h1>
      {error && <p className="text-sm text-danger">{error.message}</p>}
      {rows.length === 0 ? (
        <div className="card p-8 text-center text-sm text-muted">
          Nothing liked yet. Likes are kept permanently and will drive keyword suggestions later.
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-4">
          {rows.map(({ listing }) => (
            <ListingCard key={listing.id} listing={listing} liked />
          ))}
        </div>
      )}
    </div>
  );
}
