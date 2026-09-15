create table if not exists public.guild_settings (
  guild_id bigint primary key,
  channel_id bigint,
  role_id bigint,
  team_id text,
  team_name text
);

alter table public.guild_settings add column if not exists team_id text;
alter table public.guild_settings add column if not exists team_name text;

create table if not exists public.announced_matches (
  match_id text primary key,
  announced_at timestamptz not null default now()
);
