-- Horse Events: shared/multi-tenant calendar schema.
--
-- Run this once in the Supabase SQL editor (Dashboard > SQL Editor > New query
-- > paste this whole file > Run). Safe to re-run: everything is IF NOT EXISTS
-- or CREATE OR REPLACE.
--
-- Design notes:
--  - Events are scraped ONCE per real-world venue and shared across every
--    list that includes it (list_venues is the fan-out), not duplicated
--    per list.
--  - The public (anon) key is only ever granted EXECUTE on the two
--    SECURITY DEFINER functions below (create_list, delete_list_if_owner)
--    plus plain SELECT on the four tables for reading a shared calendar.
--    It never gets direct INSERT/UPDATE/DELETE on any table -- that keeps
--    the whole "who can write what" question to two small functions instead
--    of a pile of per-table RLS policies to get right.
--  - Horse Monkey has no stable venue id, only a free-text venue_name
--    string, and its search API's "operator" field isn't actually enforced
--    server-side (confirmed live: "equals" with a partial value still
--    matches multiple venues, same as "contains"). So `canonical_venue_name`
--    stores the exact venue_name Horse Monkey returned when a venue was
--    added, and the scraper re-fetches by substring search, then filters
--    the returned rows to exact equality against this column itself --
--    never trust the API's operator to do that for you.

create extension if not exists pgcrypto;

create table if not exists venues (
  id uuid primary key default gen_random_uuid(),
  name text not null,                    -- display name shown in the app
  canonical_venue_name text not null,    -- exact Horse Monkey venue_name string, used for exact-match re-scraping
  source text not null default 'horsemonkey',  -- 'horsemonkey' | 'horse-events'
  external_ref text,                     -- horse-events.co.uk's venue slug; null for horsemonkey (canonical_venue_name is its own identifier)
  created_at timestamptz not null default now(),
  unique (source, canonical_venue_name)
);

create table if not exists lists (
  id uuid primary key default gen_random_uuid(),
  name text,
  owner_token uuid not null default gen_random_uuid(),
  created_at timestamptz not null default now()
);

create table if not exists list_venues (
  list_id uuid not null references lists(id) on delete cascade,
  venue_id uuid not null references venues(id) on delete cascade,
  primary key (list_id, venue_id)
);

create table if not exists events (
  id text primary key,             -- same deterministic ids the scraper already generates, e.g. "venue-hm-12345"
  venue_id uuid not null references venues(id) on delete cascade,
  title text not null,
  date date not null,
  end_date date,
  time text,
  type text,
  url text,
  notes text default '',
  source text not null default 'auto',
  updated_at timestamptz not null default now()
);
create index if not exists events_venue_id_idx on events(venue_id);
create index if not exists events_date_idx on events(date);

alter table venues enable row level security;
alter table lists enable row level security;
alter table list_venues enable row level security;
alter table events enable row level security;

-- Anyone can read: needed for the shared calendar view and the "does this
-- venue already exist" check before adding a new one.
drop policy if exists venues_select on venues;
create policy venues_select on venues for select using (true);
drop policy if exists lists_select on lists;
create policy lists_select on lists for select using (true);
drop policy if exists list_venues_select on list_venues;
create policy list_venues_select on list_venues for select using (true);
drop policy if exists events_select on events;
create policy events_select on events for select using (true);

-- No insert/update/delete policies for anon on any table -- all writes go
-- through the SECURITY DEFINER functions below, or (for `events`) through
-- the scraper's own service-role key, which bypasses RLS entirely.

-- Finds an existing venue by (source, canonical name) or creates it.
-- Called from the search page when a visitor picks a search result.
create or replace function get_or_create_venue(
  p_name text,
  p_canonical_venue_name text,
  p_source text default 'horsemonkey',
  p_external_ref text default null
) returns uuid
language plpgsql
security definer
set search_path = public
as $$
declare
  v_id uuid;
begin
  insert into venues (name, canonical_venue_name, source, external_ref)
  values (p_name, p_canonical_venue_name, p_source, p_external_ref)
  on conflict (source, canonical_venue_name) do update
    set name = excluded.name,
        external_ref = excluded.external_ref
  returning id into v_id;
  return v_id;
end;
$$;
grant execute on function get_or_create_venue(text, text, text, text) to anon;

-- Creates a list from a set of venue ids, returns its id + owner_token
-- (the owner_token is only ever shown to the creator, once, in the URL).
create or replace function create_list(
  p_venue_ids uuid[],
  p_name text default null
) returns table (id uuid, owner_token uuid)
language plpgsql
security definer
set search_path = public
as $$
declare
  v_list_id uuid;
  v_owner_token uuid;
begin
  if p_venue_ids is null or array_length(p_venue_ids, 1) is null then
    raise exception 'At least one venue is required';
  end if;

  insert into lists (name) values (p_name) returning lists.id, lists.owner_token
    into v_list_id, v_owner_token;

  insert into list_venues (list_id, venue_id)
  select v_list_id, v from unnest(p_venue_ids) as v
  on conflict do nothing;

  return query select v_list_id, v_owner_token;
end;
$$;
grant execute on function create_list(uuid[], text) to anon;

-- Deletes a list only if the caller supplies its correct owner_token --
-- the one self-service action a list creator can take without any login.
create or replace function delete_list_if_owner(
  p_list_id uuid,
  p_owner_token uuid
) returns boolean
language plpgsql
security definer
set search_path = public
as $$
begin
  delete from lists where id = p_list_id and owner_token = p_owner_token;
  return found;
end;
$$;
grant execute on function delete_list_if_owner(uuid, uuid) to anon;
