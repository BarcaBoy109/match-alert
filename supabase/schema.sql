create table if not exists public.guild_settings (
  guild_id bigint primary key,
  channel_id bigint,
  role_id bigint,
  team_id text,
  team_name text
);

alter table public.guild_settings add column if not exists team_id text;
alter table public.guild_settings add column if not exists team_name text;

create table if not exists public.guild_teams (
  guild_id bigint not null,
  team_id text not null,
  team_name text not null,
  primary key (guild_id, team_id)
);

insert into public.guild_teams (guild_id, team_id, team_name)
select guild_id, team_id, team_name
from public.guild_settings
where team_id is not null and team_name is not null
on conflict (guild_id, team_id) do nothing;

create table if not exists public.announced_matches (
  match_id text primary key,
  announced_at timestamptz not null default now()
);
