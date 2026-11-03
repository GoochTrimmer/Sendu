-- Email-based password links were removed (passwords are set by the admin in
-- Supabase, and changed in Settings), so this lookup is no longer used.
drop function if exists public.email_registered(text);
