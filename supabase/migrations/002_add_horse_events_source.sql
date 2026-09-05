-- Adds support for a second searchable source (horse-events.co.uk) alongside
-- Horse Monkey. Run this once in the Supabase SQL editor -- safe to re-run.
--
-- horse-events.co.uk identifies a venue by a URL slug (e.g.
-- "allens-hill-equestrian-centre"), not by an exact name string the way
-- Horse Monkey does -- external_ref stores that slug. It's null for
-- horsemonkey-sourced venues, which don't need it (canonical_venue_name is
-- already their identifier).

alter table venues add column if not exists external_ref text;

-- Postgres treats a function's argument list as part of its identity, so
-- adding a parameter creates a separate *overload* rather than replacing
-- the original -- drop the old 3-arg signature explicitly first so there's
-- only ever one get_or_create_venue, not two confusing overloads.
drop function if exists get_or_create_venue(text, text, text);

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
