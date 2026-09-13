create table if not exists public.guild_settings (
  guild_id bigint primary key,
  channel_id bigint,
  role_id bigint
);

create table if not exists public.announced_matches (
  match_id text primary key,
  announced_at timestamptz not null default now()
);
