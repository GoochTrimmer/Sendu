import { createClient } from "@/lib/supabase/server";
import { formatTime, marketplaceName, timeAgo } from "@/lib/format";

const STALE_MS = 3 * 60 * 60 * 1000;

/** Warns when the worker hasn't finished a successful run in 3 h, or a marketplace is paused. */
export async function StatusBanner() {
  const supabase = await createClient();
  const { data: claims } = await supabase.auth.getClaims();
  if (!claims?.claims?.sub) return null;

  const [{ data: hb }, { data: mps }] = await Promise.all([
    supabase.from("worker_heartbeat").select("last_success_at,last_status,last_finished_at").maybeSingle(),
    supabase.from("marketplaces").select("code,name,paused_until,last_error").gt("paused_until", new Date().toISOString()),
  ]);

  const messages: string[] = [];
  const lastOk = hb?.last_success_at as string | null | undefined;
  // eslint-disable-next-line react-hooks/purity -- server component, rendered per request
  const now = Date.now();
  if (!lastOk) {
    messages.push("The worker hasn't completed a run yet.");
  } else if (now - new Date(lastOk).getTime() > STALE_MS) {
    messages.push(`The worker's last successful run was ${timeAgo(lastOk, now)}. Alerts may be delayed.`);
  }
  for (const mp of mps ?? []) {
    messages.push(
      `${mp.name ?? marketplaceName(mp.code)} paused until ${formatTime(mp.paused_until)}` +
        (mp.last_error ? `: ${mp.last_error}` : ""),
    );
  }
  if (!messages.length) return null;

  return (
    <div className="border-b border-border bg-warn-bg text-warn-text">
      <div className="mx-auto max-w-6xl space-y-0.5 px-4 py-2 text-sm">
        {messages.map((m) => (
          <p key={m}>⚠ {m}</p>
        ))}
      </div>
    </div>
  );
}
