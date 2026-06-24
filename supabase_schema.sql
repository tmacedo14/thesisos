-- ThesisOS Cloud Sync schema
-- Execute once in Supabase > SQL Editor.

create table if not exists public.thesisos_state (
  workspace_id text not null,
  namespace text not null,
  payload jsonb not null,
  version bigint not null default 1,
  updated_at timestamptz not null default now(),
  primary key (workspace_id, namespace),
  constraint thesisos_state_namespace_not_blank check (length(trim(namespace)) > 0),
  constraint thesisos_state_workspace_not_blank check (length(trim(workspace_id)) > 0)
);

create or replace function public.set_thesisos_state_updated_at()
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

drop trigger if exists thesisos_state_updated_at on public.thesisos_state;
create trigger thesisos_state_updated_at
before update on public.thesisos_state
for each row execute function public.set_thesisos_state_updated_at();

alter table public.thesisos_state enable row level security;

-- The browser never accesses this table directly. No anon/authenticated policy is created.
revoke all on table public.thesisos_state from anon, authenticated;
grant select, insert, update, delete on table public.thesisos_state to service_role;

comment on table public.thesisos_state is
  'Server-side ThesisOS state snapshots. Accessed only through the ThesisOS backend.';
