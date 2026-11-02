-- Reliable 30-minute trigger for the GitHub "poll" workflow.
--
-- GitHub's own `schedule:` trigger is best-effort and can be delayed or
-- skipped for hours, especially on new repos. This uses Supabase's pg_cron +
-- pg_net to call GitHub's workflow_dispatch API on time instead. The workflow
-- keeps its own schedule as a fallback; duplicate runs are harmless (the
-- concurrency group serialises them and the second finds nothing due).
--
-- BEFORE running this file, store a GitHub fine-grained token in Vault
-- (run separately in the SQL editor; never commit the token):
--
--   select vault.create_secret('github_pat_...', 'github_dispatch_token');
--
-- Token: Repository access = only <owner>/Sendu; Permissions: Actions = Read and write.
-- To rotate:  select vault.update_secret(
--               (select id from vault.secrets where name = 'github_dispatch_token'), 'github_pat_NEW');

create extension if not exists pg_cron;
create extension if not exists pg_net;

-- Re-runnable: drop any earlier version of the job first.
select cron.unschedule(jobid) from cron.job where jobname = 'sendu-poll';

select cron.schedule(
  'sendu-poll',
  '7,37 * * * *',
  $job$
  select net.http_post(
    url := 'https://api.github.com/repos/GoochTrimmer/Sendu/actions/workflows/poll.yml/dispatches',
    headers := jsonb_build_object(
      'Authorization', 'Bearer ' || (select decrypted_secret from vault.decrypted_secrets
                                     where name = 'github_dispatch_token'),
      'Accept', 'application/vnd.github+json',
      'X-GitHub-Api-Version', '2022-11-28',
      'User-Agent', 'sendu-pg-cron'
    ),
    body := '{"ref":"main"}'::jsonb
  );
  $job$
);

-- Check it worked (GitHub answers 204 on success):
--   select created, status_code, content from net._http_response order by created desc limit 5;
