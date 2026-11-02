import { MARKETPLACES } from "./types";

export function marketplaceName(code: string): string {
  return MARKETPLACES.find((m) => m.code === code)?.name ?? code;
}

export function formatPrice(price: number | null, currency: string | null): string {
  if (price == null) return "—";
  const cur = currency ?? "";
  const digits = cur === "JPY" || cur === "KRW" ? 0 : price % 1 === 0 ? 0 : 2;
  try {
    return new Intl.NumberFormat("en", {
      style: currency ? "currency" : "decimal",
      currency: currency ?? undefined,
      maximumFractionDigits: digits,
      minimumFractionDigits: digits,
    }).format(price);
  } catch {
    return `${price} ${cur}`.trim();
  }
}

export function timeAgo(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return "—";
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

export function formatTime(iso: string | null): string {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en-SG", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "Asia/Singapore",
  }).format(new Date(iso));
}

/** Split comma / newline separated input into clean terms. */
export function parseTerms(raw: FormDataEntryValue | null): string[] {
  if (typeof raw !== "string") return [];
  return raw
    .split(/[,\n、，]/)
    .map((t) => t.trim())
    .filter(Boolean);
}

export function joinTerms(terms: string[] | null | undefined): string {
  return (terms ?? []).join(", ");
}

export interface ParsedSeller {
  marketplace: string;
  sellerId: string;
  sellerName: string | null;
}

/**
 * Parse a pasted seller profile URL (or a bare ID) into marketplace + seller ID.
 * Grailed profile URLs only contain the username, so those become `name:<username>`
 * entries, which the worker matches against the seller's username. Blocking from
 * a feed card always stores the real numeric ID.
 */
export function parseSellerInput(input: string, fallbackMarketplace: string): ParsedSeller | null {
  const raw = input.trim();
  if (!raw) return null;
  let url: URL | null = null;
  try {
    url = new URL(raw.startsWith("http") ? raw : `https://${raw}`);
    if (!url.hostname.includes(".")) url = null;
  } catch {
    url = null;
  }
  if (url) {
    const host = url.hostname.replace(/^www\./, "");
    const parts = url.pathname.split("/").filter(Boolean);
    if (host.endsWith("mercari.com")) {
      const i = parts.indexOf("profile");
      const id = i >= 0 ? parts[i + 1] : parts[parts.length - 1];
      return id ? { marketplace: "mercari_jp", sellerId: id, sellerName: null } : null;
    }
    if (host.endsWith("auctions.yahoo.co.jp")) {
      // Seller pages: /seller/<id>. Blocking from a feed card is more reliable, since
      // search results carry Yahoo's opaque seller id.
      const i = parts.indexOf("seller");
      const id = i >= 0 ? parts[i + 1] : null;
      return id ? { marketplace: "yahoo_auctions", sellerId: id, sellerName: null } : null;
    }
    if (host.endsWith("grailed.com")) {
      const name = parts[0] === "users" ? parts[1] : parts[0];
      if (!name || name === "listings") return null;
      if (/^\d+$/.test(name)) return { marketplace: "grailed", sellerId: name, sellerName: null };
      return { marketplace: "grailed", sellerId: `name:${name}`, sellerName: name };
    }
    if (host.includes("ebay.")) {
      const i = parts.indexOf("usr");
      const name = i >= 0 ? parts[i + 1] : url.searchParams.get("_ssn") ?? parts[parts.length - 1];
      return name ? { marketplace: "ebay", sellerId: name, sellerName: name } : null;
    }
    return null;
  }
  return { marketplace: fallbackMarketplace, sellerId: raw, sellerName: null };
}
