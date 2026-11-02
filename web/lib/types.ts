export const MARKETPLACES = [
  { code: "ebay", name: "eBay" },
  { code: "grailed", name: "Grailed" },
  { code: "mercari_jp", name: "Mercari JP" },
  { code: "yahoo_auctions", name: "Yahoo! Auctions" },
] as const;

export type MarketplaceCode = (typeof MARKETPLACES)[number]["code"];
export type Priority = "urgent" | "normal" | "daily";
export const PRIORITIES: Priority[] = ["urgent", "normal", "daily"];
export const CURRENCIES = ["SGD", "USD", "EUR", "GBP", "JPY", "AUD", "HKD"] as const;

export interface Listing {
  id: string;
  marketplace: string;
  external_id: string;
  title: string;
  description_snippet: string | null;
  price: number | null;
  previous_price: number | null;
  price_dropped_at: string | null;
  currency: string | null;
  size: string | null;
  brand: string | null;
  seller_id: string | null;
  seller_name: string | null;
  url: string;
  image_urls: string[];
  listed_at: string | null;
  status: "active" | "sold" | "removed";
  is_bumped: boolean;
  first_seen_at: string;
}

export interface SearchOverride {
  search_id: string;
  marketplace: string;
  query_override: string | null;
  exclude_enabled: boolean | null;
  extra_exclude_terms: string[];
}

export interface SavedSearch {
  id: string;
  user_id: string;
  title: string;
  query: string;
  group_name: string | null;
  marketplaces: string[];
  include_terms: string[];
  exclude_terms: string[];
  exclude_enabled: boolean;
  spam_filter_enabled: boolean;
  min_price: number | null;
  max_price: number | null;
  currency: string;
  sizes: string[];
  priority: Priority;
  is_paused: boolean;
  created_at: string;
  updated_at: string;
  search_marketplace_overrides?: SearchOverride[];
}

export interface SearchStateRow {
  search_id: string;
  marketplace: string;
  initialized: boolean;
  last_polled_at: string | null;
  last_new_count: number | null;
  last_run_stats: Record<string, number | string | null> | null;
  consecutive_errors: number;
  last_error: string | null;
}

export interface MatchRow {
  id: string;
  state: "new" | "seen" | "dismissed";
  is_initial: boolean;
  matched_at: string;
  listing: Listing;
  search: { id: string; title: string; group_name: string | null };
}

export interface BlockedSellerRow {
  id: string;
  marketplace: string;
  seller_id: string;
  seller_name: string | null;
  scope: "personal" | "shared";
  owner_id: string | null;
  created_by: string;
  reason: string | null;
  created_at: string;
  creator?: { display_name: string | null } | null;
}

export interface Profile {
  id: string;
  display_name: string | null;
  telegram_chat_id: number | null;
  telegram_link_code: string | null;
  notify_mode: "instant" | "digest" | "off";
  is_admin: boolean;
}
