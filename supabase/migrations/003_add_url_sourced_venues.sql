-- Adds support for adding venues by pasting their own booking-page URL
-- (MyRidingLife/EquineAffairs, Equipe, EntryMaster Lite, EC Pro -- see
-- scripts/scrapers.py's detect_venue_url and the search-venues edge
-- function's `url` branch), plus the ability to add venues to a list after
-- it's already been created. Run this once in the Supabase SQL editor --
-- safe to re-run.
--
-- No new columns needed: for these URL-detected sources, canonical_venue_name
-- stores the same value as external_ref (the scrape identifier itself, e.g.
-- the listing URL or organizer id) rather than a human display name -- that's
-- exactly what the existing unique(source, canonical_venue_name) constraint
-- needs to correctly dedupe repeated adds of the same real-world venue.
-- `name` is unaffected and stays the free-text display name.

-- Adds venues to an existing list -- the missing piece that lets a list
-- grow over time (previously deferred: "editing a list after creation" was
-- delete-only). Mirrors delete_list_if_owner's owner-token check: only the
-- creator of a list (the one holding its owner_token) can add more venues
-- to it, matching how delete already works -- a shared read-only link alone
-- isn't enough.
create or replace function add_venues_to_list(
  p_list_id uuid,
  p_owner_token uuid,
  p_venue_ids uuid[]
) returns boolean
language plpgsql
security definer
set search_path = public
as $$
begin
  if not exists (select 1 from lists where id = p_list_id and owner_token = p_owner_token) then
    return false;
  end if;
  if p_venue_ids is null or array_length(p_venue_ids, 1) is null then
    raise exception 'At least one venue is required';
  end if;

  insert into list_venues (list_id, venue_id)
  select p_list_id, v from unnest(p_venue_ids) as v
  on conflict do nothing;

  return true;
end;
$$;
grant execute on function add_venues_to_list(uuid, uuid, uuid[]) to anon;
