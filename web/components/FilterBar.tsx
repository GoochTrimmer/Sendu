"use client";

import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { MARKETPLACES } from "@/lib/types";

interface Props {
  searches: { id: string; title: string }[];
  groups: string[];
}

export function FilterBar({ searches, groups }: Props) {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();

  function set(key: string, value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    router.replace(`${pathname}?${next.toString()}`);
  }

  const select = "input w-auto py-1.5 text-xs";
  return (
    <div className="flex flex-wrap items-center gap-2">
      <select className={select} value={params.get("search") ?? ""} onChange={(e) => set("search", e.target.value)} aria-label="Search">
        <option value="">All searches</option>
        {searches.map((s) => (
          <option key={s.id} value={s.id}>{s.title}</option>
        ))}
      </select>
      {groups.length > 0 && (
        <select className={select} value={params.get("group") ?? ""} onChange={(e) => set("group", e.target.value)} aria-label="Group">
          <option value="">All groups</option>
          {groups.map((g) => (
            <option key={g} value={g}>{g}</option>
          ))}
        </select>
      )}
      <select className={select} value={params.get("mp") ?? ""} onChange={(e) => set("mp", e.target.value)} aria-label="Marketplace">
        <option value="">All marketplaces</option>
        {MARKETPLACES.map((m) => (
          <option key={m.code} value={m.code}>{m.name}</option>
        ))}
      </select>
      <label className="flex items-center gap-1.5 text-xs text-muted">
        <input
          type="checkbox"
          checked={params.get("initial") !== "show"}
          onChange={(e) => set("initial", e.target.checked ? "" : "show")}
        />
        Hide initial results
      </label>
    </div>
  );
}
