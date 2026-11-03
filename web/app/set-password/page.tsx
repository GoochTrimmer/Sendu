import { requireUser } from "@/lib/auth";
import { SetPasswordForm } from "./SetPasswordForm";

export default async function SetPasswordPage() {
  const { supabase } = await requireUser();
  const { data } = await supabase.auth.getUser();
  const email = data.user?.email;

  return (
    <div className="mx-auto mt-16 max-w-sm">
      <h1 className="text-2xl font-bold tracking-tight">Change password</h1>
      {email && (
        <p className="mt-1 text-sm text-muted">
          For <b>{email}</b>.
        </p>
      )}
      <SetPasswordForm email={email} />
    </div>
  );
}
