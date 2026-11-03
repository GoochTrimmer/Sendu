-- Password login: lets the login page tell an invited (existing) email apart
-- from an unknown one before sending a "set / change your password" link.
-- Returns only true/false. This app is invite-only and the old magic-link flow
-- already revealed whether an email was invited, so this exposes nothing new.

create or replace function public.email_registered(p_email text)
returns boolean
language sql
stable
security definer
set search_path = ''
as $$
  select exists (
    select 1 from auth.users
    where lower(email) = lower(trim(p_email))
      and deleted_at is null
  );
$$;

revoke all on function public.email_registered(text) from public;
grant execute on function public.email_registered(text) to anon, authenticated;
