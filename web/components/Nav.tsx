import Link from "next/link";
import { createClient } from "@/lib/supabase/server";
import { signOut } from "@/app/login/actions";

const LINKS = [
  { href: "/", label: "Feed" },
  { href: "/searches", label: "Searches" },
  { href: "/liked", label: "Liked" },
  { href: "/blocklist", label: "Blocklist" },
  { href: "/settings", label: "Settings" },
];

export async function Nav() {
  const supabase = await createClient();
  const { data } = await supabase.auth.getClaims();
  const signedIn = Boolean(data?.claims?.sub);

  return (
    <header className="sticky top-0 z-20 border-b border-border bg-bg/90 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center gap-4 px-4 py-3">
        <Link href="/" className="text-base font-bold tracking-tight">
          SENDU
        </Link>
        {signedIn && (
          <>
            <nav className="-mx-1 flex flex-1 gap-1 overflow-x-auto text-sm [scrollbar-width:none]">
              {LINKS.map((l) => (
                <Link
                  key={l.href}
                  href={l.href}
                  className="whitespace-nowrap rounded-md px-2 py-1 text-muted hover:bg-surface-2 hover:text-text"
                >
                  {l.label}
                </Link>
              ))}
            </nav>
            <form action={signOut} className="hidden sm:block">
              <button className="text-xs text-muted hover:text-text">Sign out</button>
            </form>
          </>
        )}
      </div>
    </header>
  );
}
