-- Email -> lead poller: per-message idempotency.
--
-- Apply this manually in the Supabase SQL editor for the Blooms OS project
-- (project ref pqhatplothwhdanfrcrq). It is NOT auto-applied by any deploy.
--
-- The email_poller background thread records each processed email's Message-ID
-- here via the claim_email() RPC so a lead is never created twice for the same
-- message (and so non-inquiries are never re-classified). Writes happen only
-- through this SECURITY DEFINER function using the public anon key — the table
-- itself has RLS on with no client policies, so the anon/authenticated roles
-- cannot read or write it directly.

create table if not exists public.blooms_email_seen (
    message_id text primary key,
    claimed_at timestamptz not null default now()
);

alter table public.blooms_email_seen enable row level security;
-- No client policies: only the SECURITY DEFINER RPC below touches this table.

create or replace function public.claim_email(_message_id text)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
begin
    insert into public.blooms_email_seen(message_id)
    values (_message_id)
    on conflict (message_id) do nothing;
    -- FOUND is true only when the INSERT actually added a row, i.e. this caller
    -- is the one that claimed the message. Returns false if it was already seen.
    return found;
end;
$$;

grant execute on function public.claim_email(text) to anon, authenticated;

notify pgrst, 'reload schema';
