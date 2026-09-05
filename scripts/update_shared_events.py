"""
Refreshes events for the dynamic, Supabase-driven venue list (the
search-your-own-venue / shared-calendar feature) -- as opposed to
update_events.py, which handles Sarah's personal, hardcoded venue list.
Runs as its own scheduled GitHub Actions workflow so a Supabase outage or a
bug here can never block Sarah's daily personal run, and vice versa.

Supports venues added via ad-hoc venue-name search (source == "horsemonkey"
or "horse-events", identified by venues.canonical_venue_name / external_ref
respectively) as well as venues added by pasting their own booking-page URL
on a platform scripts/scrapers.py's detect_venue_url recognizes ("myridinglife",
"equipe", "entrymaster-lite", "ecpro" -- see that function's docstring for
what each stores in external_ref). Any other `source` value is skipped with
a warning; nothing in this script invents a scraper for it.

Requires two environment variables (set as GitHub Actions secrets):
    SUPABASE_URL              e.g. https://xxxxxxxx.supabase.co
    SUPABASE_SERVICE_ROLE_KEY the *service role* key (bypasses RLS) --
                               never the anon/public key, and never expose
                               this key client-side.
"""

import os
import sys
from datetime import datetime, timezone

import requests

from scrapers import (
    fetch_horsemonkey,
    fetch_horse_events,
    fetch_ics_venue_from_url,
    fetch_equipe,
    fetch_entrymaster_lite,
    fetch_ecpro,
)

SUPABASE_URL = os.environ.get("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")


def supabase_headers():
    return {
        "apikey": SUPABASE_SERVICE_ROLE_KEY,
        "Authorization": f"Bearer {SUPABASE_SERVICE_ROLE_KEY}",
        "Content-Type": "application/json",
    }


def fetch_venues(session):
    resp = session.get(
        f"{SUPABASE_URL}/rest/v1/venues",
        headers=supabase_headers(),
        params={"select": "id,name,canonical_venue_name,source,external_ref"},
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json()


def upsert_events(session, rows):
    if not rows:
        return
    resp = session.post(
        f"{SUPABASE_URL}/rest/v1/events",
        headers={**supabase_headers(), "Prefer": "resolution=merge-duplicates,return=minimal"},
        params={"on_conflict": "id"},
        json=rows,
        timeout=30,
    )
    resp.raise_for_status()


def to_supabase_row(event, venue_id, now_iso):
    """Map our internal event dict shape (from scrapers.py, which uses
    venueKey/endDate for the personal static-file pipeline) onto the
    events table's columns (venue_id/end_date)."""
    return {
        "id": event["id"],
        "venue_id": venue_id,
        "title": event["title"],
        "date": event["date"],
        "end_date": event["endDate"],
        "time": event["time"],
        "type": event["type"],
        "url": event["url"],
        "notes": event["notes"],
        "source": event["source"],
        "updated_at": now_iso,
    }


def main():
    if not SUPABASE_URL or not SUPABASE_SERVICE_ROLE_KEY:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY must be set", file=sys.stderr)
        sys.exit(1)

    session = requests.Session()
    venues = fetch_venues(session)
    now_iso = datetime.now(timezone.utc).isoformat()

    total_events = 0
    for venue in venues:
        source = venue["source"]
        try:
            if source == "horsemonkey":
                # venue["id"] (a UUID) makes each event id globally unique
                # even if two venues happen to share a Horse Monkey event id
                # range. exact_venue_name enforces exact equality against
                # the stored canonical name -- see fetch_horsemonkey's
                # docstring for why this can't be left to Horse Monkey's
                # own `operator` field.
                events = fetch_horsemonkey(
                    session,
                    venue_key=venue["id"],
                    venue_filter_value=venue["canonical_venue_name"],
                    exact_venue_name=venue["canonical_venue_name"],
                )
            elif source == "horse-events":
                if not venue.get("external_ref"):
                    print(f"WARN: skipping venue {venue['name']!r} -- horse-events source with no slug", file=sys.stderr)
                    continue
                events = fetch_horse_events(session, venue["id"], venue["external_ref"])
            elif source == "myridinglife":
                if not venue.get("external_ref"):
                    print(f"WARN: skipping venue {venue['name']!r} -- myridinglife source with no listing URL", file=sys.stderr)
                    continue
                events = fetch_ics_venue_from_url(session, venue["id"], venue["external_ref"])
            elif source == "equipe":
                if not venue.get("external_ref"):
                    print(f"WARN: skipping venue {venue['name']!r} -- equipe source with no organizer id", file=sys.stderr)
                    continue
                events = fetch_equipe(session, venue["id"], venue["external_ref"])
            elif source == "entrymaster-lite":
                if not venue.get("external_ref"):
                    print(f"WARN: skipping venue {venue['name']!r} -- entrymaster-lite source with no site URL", file=sys.stderr)
                    continue
                events = fetch_entrymaster_lite(session, venue["id"], venue["external_ref"])
            elif source == "ecpro":
                if not venue.get("external_ref"):
                    print(f"WARN: skipping venue {venue['name']!r} -- ecpro source with no site URL", file=sys.stderr)
                    continue
                events = fetch_ecpro(session, venue["id"], venue["external_ref"])
            else:
                print(f"WARN: skipping venue {venue['name']!r} -- unsupported source {source!r}", file=sys.stderr)
                continue
        except Exception as exc:  # noqa: BLE001 -- one bad venue must not block the rest
            print(f"WARN: {source}/{venue['name']} scrape failed: {exc}", file=sys.stderr)
            continue

        rows = [to_supabase_row(e, venue["id"], now_iso) for e in events]
        try:
            upsert_events(session, rows)
        except Exception as exc:  # noqa: BLE001
            print(f"WARN: failed to upsert events for {venue['name']}: {exc}", file=sys.stderr)
            continue

        total_events += len(rows)
        print(f"{venue['name']}: {len(rows)} events")

    print(f"Done. {len(venues)} venues processed, {total_events} events upserted.")


if __name__ == "__main__":
    main()
