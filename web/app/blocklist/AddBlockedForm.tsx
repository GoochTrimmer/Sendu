"use client";

import { useActionState } from "react";
import { MARKETPLACES } from "@/lib/types";
import { addBlocked, type BlockFormState } from "./actions";

export function AddBlockedForm({ scope }: { scope: "personal" | "shared" }) {
  const [state, action, pending] = useActionState<BlockFormState, FormData>(addBlocked, {});
  return (
    <form action={action} className="card space-y-3 p-4">
      <input type="hidden" name="scope" value={scope} />
      <div className="grid gap-3 sm:grid-cols-[1fr_auto]">
        <div>
          <label className="label" htmlFor="seller">Seller profile URL or ID</label>
          <input id="seller" name="seller" required placeholder="https://jp.mercari.com/user/profile/123456789" className="input" />
        </div>
        <div>
          <label className="label" htmlFor="marketplace">Marketplace (for bare IDs)</label>
          <select id="marketplace" name="marketplace" className="input">
            {MARKETPLACES.map((m) => (
              <option key={m.code} value={m.code}>{m.name}</option>
            ))}
          </select>
        </div>
      </div>
      <input name="reason" placeholder="Reason (optional), e.g. Xianyu reseller bot" className="input" />
      <div className="flex items-center gap-3">
        <button className="btn-primary" disabled={pending}>
          {pending ? "Adding…" : scope === "shared" ? "Block for everyone" : "Block for me"}
        </button>
        {state.error && <p className="text-sm text-danger">{state.error}</p>}
        {state.ok && <p className="text-sm text-good">{state.ok}</p>}
      </div>
      <p className="text-xs text-muted">
        Blocking from a feed card is the most reliable way, since it stores the seller&apos;s permanent ID.
        Grailed profile URLs only contain a username, which is matched as a fallback.
      </p>
    </form>
  );
}
