import { blockSeller, dismissMatches, toggleLike } from "@/app/actions";
import { formatPrice, marketplaceName, timeAgo } from "@/lib/format";
import type { Listing } from "@/lib/types";

export interface CardProps {
  listing: Listing;
  liked: boolean;
  /** Match ids for this listing (several searches can match one listing). */
  matchIds?: string[];
  searchTitles?: string[];
  isInitial?: boolean;
  isNew?: boolean;
}

export function ListingCard({ listing, liked, matchIds = [], searchTitles = [], isInitial, isNew }: CardProps) {
  const img = listing.image_urls[0];
  const openHref = matchIds.length ? `/go/${matchIds[0]}` : listing.url;
  const dropped =
    listing.price_dropped_at && listing.previous_price != null && listing.price != null
      ? listing.previous_price - listing.price
      : null;
  const seller = listing.seller_name ?? listing.seller_id;

  return (
    <article className="card flex flex-col overflow-hidden">
      <a href={openHref} target="_blank" rel="noopener noreferrer" className="group relative block">
        <div className="aspect-[4/5] w-full bg-surface-2">
          {img ? (
            // eslint-disable-next-line @next/next/no-img-element -- hot-linked marketplace image
            <img
              src={img}
              alt=""
              loading="lazy"
              referrerPolicy="no-referrer"
              className="h-full w-full object-cover transition-opacity group-hover:opacity-90"
            />
          ) : (
            <div className="flex h-full items-center justify-center text-xs text-muted">No image</div>
          )}
        </div>
        <div className="absolute left-1.5 top-1.5 flex flex-wrap gap-1">
          {isNew && <span className="rounded bg-accent px-1.5 py-0.5 text-[10px] font-bold text-accent-text">NEW</span>}
          {dropped != null && dropped > 0 && (
            <span className="rounded bg-good px-1.5 py-0.5 text-[10px] font-bold text-white">PRICE DROP</span>
          )}
          {listing.is_bumped && <span className="rounded bg-surface/90 px-1.5 py-0.5 text-[10px] font-semibold">BUMPED</span>}
          {listing.status !== "active" && (
            <span className="rounded bg-danger px-1.5 py-0.5 text-[10px] font-bold uppercase text-white">{listing.status}</span>
          )}
        </div>
      </a>

      <div className="flex flex-1 flex-col gap-1 p-2.5">
        <a href={openHref} target="_blank" rel="noopener noreferrer" className="line-clamp-2 text-sm font-medium leading-snug hover:underline">
          {listing.title}
        </a>
        <div className="flex items-baseline gap-1.5">
          <span className="text-sm font-bold">{formatPrice(listing.price, listing.currency)}</span>
          {dropped != null && dropped > 0 && (
            <span className="text-xs text-muted line-through">{formatPrice(listing.previous_price, listing.currency)}</span>
          )}
          {listing.size && <span className="text-xs text-muted">· {listing.size}</span>}
        </div>
        <p className="truncate text-xs text-muted">
          {marketplaceName(listing.marketplace)}
          {seller ? ` · ${seller}` : ""} · {timeAgo(listing.listed_at ?? listing.first_seen_at)}
        </p>
        {searchTitles.length > 0 && (
          <div className="flex flex-wrap gap-1">
            {searchTitles.map((t) => (
              <span key={t} className="chip">{t}</span>
            ))}
            {isInitial && <span className="chip">initial</span>}
          </div>
        )}

        <div className="mt-auto flex items-center gap-1 pt-1.5">
          <form action={toggleLike.bind(null, listing.id, liked)}>
            <button
              className={`btn btn-sm ${liked ? "border-like text-like" : ""}`}
              aria-pressed={liked}
              title={liked ? "Unlike" : "Like"}
            >
              {liked ? "♥" : "♡"}<span className="sr-only sm:not-sr-only">{liked ? "Liked" : "Like"}</span>
            </button>
          </form>
          {matchIds.length > 0 && (
            <form action={dismissMatches.bind(null, matchIds)}>
              <button className="btn btn-sm" title="Dismiss">
                ✕<span className="sr-only sm:not-sr-only">Dismiss</span>
              </button>
            </form>
          )}
          {listing.seller_id && (
            <details className="relative ml-auto">
              <summary className="btn btn-sm cursor-pointer list-none text-muted" title="Block seller">⛔</summary>
              <form
                action={blockSeller}
                className="card absolute bottom-full right-0 z-10 mb-1 w-56 space-y-2 p-3 shadow-lg"
              >
                <p className="text-xs font-semibold">Block {seller}</p>
                <input type="hidden" name="marketplace" value={listing.marketplace} />
                <input type="hidden" name="seller_id" value={listing.seller_id} />
                <input type="hidden" name="seller_name" value={listing.seller_name ?? ""} />
                <label className="flex items-center gap-2 text-xs">
                  <input type="radio" name="scope" value="personal" defaultChecked /> Just me
                </label>
                <label className="flex items-center gap-2 text-xs">
                  <input type="radio" name="scope" value="shared" /> Everyone (shared)
                </label>
                <input name="reason" placeholder="Reason (optional)" className="input py-1 text-xs" />
                <button className="btn-primary btn-sm w-full">Block seller</button>
              </form>
            </details>
          )}
        </div>
      </div>
    </article>
  );
}
