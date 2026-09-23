-- Match Alert Bot — complete Supabase schema
--
-- Paste this entire file into the Supabase SQL Editor for a new project,
-- or run it with psql. It is repeatable and does not require state.json.

create table if not exists public.guild_settings (
  guild_id bigint primary key,
  channel_id bigint,
  role_id bigint,
  team_id text,
  team_name text
);

create table if not exists public.guild_teams (
  guild_id bigint not null,
  team_id text not null,
  team_name text not null,
  channel_id bigint,
  role_id bigint,
  primary key (guild_id, team_id)
);

create table if not exists public.announced_matches (
  match_id text primary key,
  announced_at timestamptz not null default now(),
  channel_id bigint,
  message_id bigint,
  delete_after timestamptz
);

-- Durable fixture observations, independent of Discord message retention.
create table if not exists public.match_lifecycle (
  guild_id bigint not null,
  event_id text not null,
  kickoff timestamptz,
  status text not null,
  team_ids text[] not null default '{}',
  observed_at timestamptz not null default now(),
  primary key (guild_id, event_id)
);

-- One delivery record per guild, ESPN event, and destination channel.
create table if not exists public.alert_deliveries (
  guild_id bigint not null,
  event_id text not null,
  channel_id bigint not null,
  message_id bigint,
  delivered_at timestamptz not null default now(),
  delete_after timestamptz,
  primary key (guild_id, event_id, channel_id)
);

-- Compatibility migration for installations that already have guild_settings.
insert into public.guild_teams (guild_id, team_id, team_name)
select guild_id, team_id, team_name
from public.guild_settings
where team_id is not null and team_name is not null
on conflict (guild_id, team_id) do nothing;

-- Compatibility backfill for legacy announcement rows with a known channel.
insert into public.alert_deliveries (
  guild_id, event_id, channel_id, message_id, delete_after
)
select
  split_part(match_id, ':', 1)::bigint,
  split_part(match_id, ':', 2),
  channel_id,
  message_id,
  delete_after
from public.announced_matches
where channel_id is not null
  and split_part(match_id, ':', 1) ~ '^[0-9]+$'
  and split_part(match_id, ':', 2) <> ''
on conflict (guild_id, event_id, channel_id) do nothing;

create index if not exists match_lifecycle_observed_at_idx
  on public.match_lifecycle (observed_at);

create index if not exists alert_deliveries_delete_after_idx
  on public.alert_deliveries (delete_after)
  where delete_after is not null;
