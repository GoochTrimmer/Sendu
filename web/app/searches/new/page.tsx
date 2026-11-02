import { requireUser } from "@/lib/auth";
import { SearchForm } from "../SearchForm";

export default async function NewSearchPage() {
  const { supabase, userId } = await requireUser();
  const { data } = await supabase.from("saved_searches").select("group_name").eq("user_id", userId);
  const groups = [...new Set((data ?? []).map((r) => r.group_name as string | null).filter(Boolean))] as string[];
  return (
    <div className="mx-auto max-w-2xl space-y-4">
      <h1 className="text-xl font-bold tracking-tight">New search</h1>
      <SearchForm groups={groups} />
    </div>
  );
}
