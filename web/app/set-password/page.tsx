import { requireUser } from "@/lib/auth";
import { SetPasswordForm } from "./SetPasswordForm";

export default async function SetPasswordPage() {
  const { supabase } = await requireUser();
  const { data } = await supabase.auth.getUser();
  const email = data.user?.email;

  return (
    <div className="mx-auto mt-16 max-w-sm">
      <h1 className="text-2xl font-bold tracking-tight">Set your password</h1>
      <p className="mt-1 text-sm text-muted">
        {email ? (
          <>
            For <b>{email}</b>. You’ll use it to sign in from now on.
          </>
        ) : (
          "You’ll use it to sign in from now on."
        )}
      </p>
      <SetPasswordForm email={email} />
    </div>
  );
}
