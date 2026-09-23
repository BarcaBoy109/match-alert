-- Match Alert Bot: first four roadmap improvements
-- Safe to run repeatedly. Review in a non-production project first.

-- Per-team routing overrides. NULL means inherit the guild default.
alter table if exists public.guild_teams
  add column if not exists channel_id bigint;
alter table if exists public.guild_teams
  add column if not exists role_id bigint;

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

-- Backfill legacy announcements that have a known destination channel.
-- Rows with malformed legacy IDs are ignored rather than aborting the migration.
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

-- Helpful lookup indexes for maintenance and cleanup.
create index if not exists match_lifecycle_observed_at_idx
  on public.match_lifecycle (observed_at);
create index if not exists alert_deliveries_delete_after_idx
  on public.alert_deliveries (delete_after)
  where delete_after is not null;
