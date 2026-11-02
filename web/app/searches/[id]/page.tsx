import { notFound } from "next/navigation";
import { requireUser } from "@/lib/auth";
import type { SavedSearch } from "@/lib/types";
import { deleteSearch, duplicateSearch } from "../actions";
import { SearchForm } from "../SearchForm";

export default async function EditSearchPage(props: PageProps<"/searches/[id]">) {
  const { id } = await props.params;
  const { supabase, userId } = await requireUser();
  const [{ data: search }, { data: all }] = await Promise.all([
    supabase.from("saved_searches").select("*,search_marketplace_overrides(*)").eq("id", id).maybeSingle(),
    supabase.from("saved_searches").select("group_name").eq("user_id", userId),
  ]);
  if (!search) notFound();
  const groups = [...new Set((all ?? []).map((r) => r.group_name as string | null).filter(Boolean))] as string[];

  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <div className="flex items-center justify-between gap-2">
        <h1 className="truncate text-xl font-bold tracking-tight">Edit search</h1>
        <div className="flex gap-2">
          <form action={duplicateSearch.bind(null, id)}>
            <button className="btn btn-sm">Duplicate</button>
          </form>
          <details className="relative">
            <summary className="btn btn-sm cursor-pointer list-none text-danger">Delete</summary>
            <form action={deleteSearch.bind(null, id)} className="card absolute right-0 z-10 mt-1 w-56 space-y-2 p-3 shadow-lg">
              <p className="text-xs">Delete this search and its matches? Liked listings stay in Liked.</p>
              <button className="btn btn-sm w-full border-danger text-danger">Yes, delete</button>
            </form>
          </details>
        </div>
      </div>
      <p className="text-xs text-muted">
        Changing the query re-runs the silent first poll, so you won&apos;t get a flood of alerts.
      </p>
      <SearchForm search={search as SavedSearch} groups={groups} />
    </div>
  );
}
