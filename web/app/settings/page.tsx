import { signOut } from "@/app/login/actions";
import { requireUser } from "@/lib/auth";
import type { Profile } from "@/lib/types";
import { generateLinkCode, saveSettings } from "./actions";

const MODES = [
  { value: "instant", label: "Instant", help: "One message per new match (a digest if more than 5 arrive at once)." },
  { value: "digest", label: "Digest", help: "One summary message per worker run." },
  { value: "off", label: "Off", help: "No Telegram messages. Check the feed instead." },
];

export default async function SettingsPage() {
  const { supabase, userId } = await requireUser();
  const { data } = await supabase.from("profiles").select("*").eq("id", userId).single();
  const profile = data as Profile | null;
  const bot = process.env.NEXT_PUBLIC_TELEGRAM_BOT_USERNAME;

  return (
    <div className="mx-auto max-w-2xl space-y-6">
      <h1 className="text-xl font-bold tracking-tight">Settings</h1>

      <form action={saveSettings} className="card space-y-4 p-4">
        <div>
          <label className="label" htmlFor="display_name">Display name</label>
          <input id="display_name" name="display_name" defaultValue={profile?.display_name ?? ""} className="input" />
          <p className="mt-1 text-xs text-muted">Shown next to shared blocklist entries you add.</p>
        </div>
        <fieldset>
          <legend className="label">Notifications</legend>
          <div className="space-y-2">
            {MODES.map((m) => (
              <label key={m.value} className="flex items-start gap-2 text-sm">
                <input type="radio" name="notify_mode" value={m.value} defaultChecked={(profile?.notify_mode ?? "instant") === m.value} className="mt-1" />
                <span>
                  {m.label}
                  <span className="block text-xs text-muted">{m.help}</span>
                </span>
              </label>
            ))}
          </div>
        </fieldset>
        <button className="btn-primary">Save</button>
      </form>

      <section className="card space-y-3 p-4">
        <h2 className="text-sm font-semibold">Telegram</h2>
        {profile?.telegram_chat_id ? (
          <p className="text-sm text-good">✓ Linked. Alerts go to your Telegram chat.</p>
        ) : (
          <p className="text-sm text-muted">Not linked yet.</p>
        )}
        {profile?.telegram_link_code ? (
          <div className="space-y-2 text-sm">
            <p>
              Send this to {bot ? <b>@{bot}</b> : "the SENDU bot"} on Telegram:
            </p>
            <code className="block rounded-lg bg-surface-2 px-3 py-2 font-mono text-base">/start {profile.telegram_link_code}</code>
            {bot && (
              <a className="btn" href={`https://t.me/${bot}?start=${profile.telegram_link_code}`} target="_blank" rel="noopener noreferrer">
                Open in Telegram
              </a>
            )}
            <p className="text-xs text-muted">The link completes on the worker&apos;s next run (within about 30 minutes).</p>
          </div>
        ) : null}
        <form action={generateLinkCode}>
          <button className="btn">{profile?.telegram_chat_id ? "Re-link Telegram" : "Generate link code"}</button>
        </form>
      </section>

      <form action={signOut}>
        <button className="btn">Sign out</button>
      </form>
    </div>
  );
}
