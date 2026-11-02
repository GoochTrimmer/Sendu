"use client";

import Link from "next/link";
import { useActionState, useState } from "react";
import { joinTerms } from "@/lib/format";
import { CURRENCIES, MARKETPLACES, PRIORITIES, type SavedSearch } from "@/lib/types";
import { saveSearch, type SearchFormState } from "./actions";

// Marketplaces pre-ticked on a new search: the ones currently working (eBay deferred, Grailed pending).
const DEFAULT_MARKETPLACES = ["mercari_jp", "yahoo_auctions"];

const PRIORITY_HELP: Record<string, string> = {
  urgent: "every ~30 min",
  normal: "every ~2 h",
  daily: "once a day",
};

export function SearchForm({ search, groups }: { search?: SavedSearch; groups: string[] }) {
  const [state, action, pending] = useActionState<SearchFormState, FormData>(
    saveSearch.bind(null, search?.id ?? null),
    {},
  );
  const [selected, setSelected] = useState<string[]>(search?.marketplaces ?? DEFAULT_MARKETPLACES);
  const overrides = Object.fromEntries((search?.search_marketplace_overrides ?? []).map((o) => [o.marketplace, o]));

  return (
    <form action={action} className="space-y-6">
      <section className="card space-y-4 p-4">
        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <label className="label" htmlFor="query">Query</label>
            <input id="query" name="query" required defaultValue={search?.query} placeholder="yohji pour homme" className="input" />
          </div>
          <div>
            <label className="label" htmlFor="title">Title</label>
            <input id="title" name="title" defaultValue={search?.title} placeholder="Defaults to the query" className="input" />
          </div>
          <div>
            <label className="label" htmlFor="group_name">Group</label>
            <input id="group_name" name="group_name" list="groups" defaultValue={search?.group_name ?? ""} placeholder="e.g. Yohji" className="input" />
            <datalist id="groups">
              {groups.map((g) => <option key={g} value={g} />)}
            </datalist>
          </div>
          <div>
            <label className="label" htmlFor="priority">Priority</label>
            <select id="priority" name="priority" defaultValue={search?.priority ?? "normal"} className="input">
              {PRIORITIES.map((p) => (
                <option key={p} value={p}>{p} ({PRIORITY_HELP[p]})</option>
              ))}
            </select>
          </div>
        </div>
      </section>

      <section className="card space-y-3 p-4">
        <h2 className="text-sm font-semibold">Marketplaces</h2>
        <div className="flex flex-wrap gap-3">
          {MARKETPLACES.map((m) => (
            <label key={m.code} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                name="marketplaces"
                value={m.code}
                checked={selected.includes(m.code)}
                onChange={(e) =>
                  setSelected((s) => (e.target.checked ? [...s, m.code] : s.filter((c) => c !== m.code)))
                }
              />
              {m.name}
            </label>
          ))}
        </div>
        {MARKETPLACES.filter((m) => selected.includes(m.code)).map((m) => {
          const o = overrides[m.code];
          const ex = o?.exclude_enabled == null ? "inherit" : o.exclude_enabled ? "on" : "off";
          return (
            <details key={m.code} className="rounded-lg border border-border p-3" open={Boolean(o)}>
              <summary className="cursor-pointer text-sm font-medium">{m.name} overrides</summary>
              <div className="mt-3 grid gap-3 sm:grid-cols-3">
                <div className="sm:col-span-3">
                  <label className="label">Query override</label>
                  <input
                    name={`override_query_${m.code}`}
                    defaultValue={o?.query_override ?? ""}
                    placeholder={
                      m.code === "mercari_jp" || m.code === "yahoo_auctions"
                        ? "ヨウジヤマモト プールオム"
                        : "Uses the main query"
                    }
                    className="input"
                  />
                </div>
                <div>
                  <label className="label">Exclude terms</label>
                  <select name={`override_exclude_${m.code}`} defaultValue={ex} className="input">
                    <option value="inherit">Same as search</option>
                    <option value="on">On</option>
                    <option value="off">Off</option>
                  </select>
                </div>
                <div className="sm:col-span-2">
                  <label className="label">Extra exclude terms</label>
                  <input name={`override_extra_${m.code}`} defaultValue={joinTerms(o?.extra_exclude_terms)} placeholder="comma separated" className="input" />
                </div>
              </div>
            </details>
          );
        })}
      </section>

      <section className="card space-y-4 p-4">
        <h2 className="text-sm font-semibold">Filters</h2>
        <div>
          <label className="label" htmlFor="include_terms">Must include (all of)</label>
          <textarea id="include_terms" name="include_terms" rows={2} defaultValue={joinTerms(search?.include_terms)} placeholder="Optional. Comma or newline separated" className="input" />
        </div>
        <div>
          <div className="flex items-center justify-between">
            <label className="label" htmlFor="exclude_terms">Exclude (any of)</label>
            <label className="flex items-center gap-1.5 text-xs text-muted">
              <input type="checkbox" name="exclude_enabled" defaultChecked={search?.exclude_enabled ?? true} />
              Enabled
            </label>
          </div>
          <textarea id="exclude_terms" name="exclude_terms" rows={2} defaultValue={joinTerms(search?.exclude_terms)} placeholder="replica, inspired, y-3" className="input" />
        </div>
        <label className="flex items-start gap-2 text-sm">
          <input type="checkbox" name="spam_filter_enabled" defaultChecked={search?.spam_filter_enabled ?? true} className="mt-0.5" />
          <span>
            Spam filter
            <span className="block text-xs text-muted">Hide keyword-stuffed listings that name 3+ different brands.</span>
          </span>
        </label>
        <div className="grid grid-cols-3 gap-3">
          <div>
            <label className="label" htmlFor="min_price">Min price</label>
            <input id="min_price" name="min_price" inputMode="decimal" defaultValue={search?.min_price ?? ""} className="input" />
          </div>
          <div>
            <label className="label" htmlFor="max_price">Max price</label>
            <input id="max_price" name="max_price" inputMode="decimal" defaultValue={search?.max_price ?? ""} className="input" />
          </div>
          <div>
            <label className="label" htmlFor="currency">Currency</label>
            <select id="currency" name="currency" defaultValue={search?.currency ?? "SGD"} className="input">
              {CURRENCIES.map((c) => <option key={c}>{c}</option>)}
            </select>
          </div>
        </div>
        <div>
          <label className="label" htmlFor="sizes">Sizes</label>
          <input id="sizes" name="sizes" defaultValue={joinTerms(search?.sizes)} placeholder="e.g. 3, L, 50. Listings with no size are kept" className="input" />
        </div>
      </section>

      {state.error && <p className="text-sm text-danger">{state.error}</p>}
      <div className="flex gap-2">
        <button className="btn-primary" disabled={pending}>{pending ? "Saving…" : search ? "Save changes" : "Create search"}</button>
        <Link href="/searches" className="btn">Cancel</Link>
      </div>
    </form>
  );
}
