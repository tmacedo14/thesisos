-- ThesisOS Supabase Auth migration
-- Run once in Supabase > SQL Editor after the original supabase_schema.sql.
-- Creates the isolated per-user state table used by ThesisOS.

create table if not exists public.thesisos_user_state (
  user_id uuid not null references auth.users(id) on delete cascade,
  namespace text not null,
  payload jsonb not null,
  version bigint not null default 1,
  updated_at timestamptz not null default now(),
  primary key (user_id, namespace),
  constraint thesisos_user_state_namespace_not_blank
    check (length(trim(namespace)) > 0)
);

create or replace function public.set_thesisos_user_state_updated_at()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
begin
  new.updated_at = now();
  return new;
end;
$$;

drop trigger if exists thesisos_user_state_updated_at
  on public.thesisos_user_state;

create trigger thesisos_user_state_updated_at
before update on public.thesisos_user_state
for each row execute function public.set_thesisos_user_state_updated_at();

alter table public.thesisos_user_state enable row level security;

revoke all on table public.thesisos_user_state from anon;
grant select, insert, update, delete
  on table public.thesisos_user_state
  to authenticated, service_role;

drop policy if exists "Users read own ThesisOS state"
  on public.thesisos_user_state;
create policy "Users read own ThesisOS state"
on public.thesisos_user_state
for select
to authenticated
using ((select auth.uid()) = user_id);

drop policy if exists "Users insert own ThesisOS state"
  on public.thesisos_user_state;
create policy "Users insert own ThesisOS state"
on public.thesisos_user_state
for insert
to authenticated
with check ((select auth.uid()) = user_id);

drop policy if exists "Users update own ThesisOS state"
  on public.thesisos_user_state;
create policy "Users update own ThesisOS state"
on public.thesisos_user_state
for update
to authenticated
using ((select auth.uid()) = user_id)
with check ((select auth.uid()) = user_id);

drop policy if exists "Users delete own ThesisOS state"
  on public.thesisos_user_state;
create policy "Users delete own ThesisOS state"
on public.thesisos_user_state
for delete
to authenticated
using ((select auth.uid()) = user_id);

create index if not exists thesisos_user_state_updated_at_idx
  on public.thesisos_user_state (updated_at desc);

comment on table public.thesisos_user_state is
  'Per-user ThesisOS state protected by Supabase Auth and Row Level Security.';
