"""
Refreshes data/events.json from each venue's public booking calendar.

This is Sarah's personal, hardcoded venue list -- the actual scraping logic
lives in scripts/scrapers.py (shared with scripts/update_shared_events.py,
which does the same job for the dynamic/shared-calendar venue list). Only
touches events tagged "source": "auto": Moreton Morrell, Swallowfield,
Solihull RC, Walsgrave ARC, Swalcliffe Park and CCR Equestrian (native
booking-platform events), Onley (Equipe organizer page -- their real system
of record), Rugby Riding Club (their own EntryMaster Lite site), The
Unicorn Equestrian Centre (their own "What's On" page -- Pony Club and dog
agility entries filtered out, Sarah isn't a Pony Club member and doesn't do
dog agility), Cotswold Cup legs at those venues plus Offchurch Bury/Hazleton
Manor/Waverton House/Cirencester Park, and horse-events.co.uk listings at
Swalcliffe, Moreton Morrell, Solihull RC, Dallas Burston, Barcheston, Aston
Le Walls, Onley, and Rugby Riding Club (Pony Club-restricted events are
filtered out of the horse-events.co.uk listings too), and Lowlands
Equestrian Centre (an RDA centre with its own EC Pro booking site --
lists both open unaffiliated shows and recurring RDA clinics). Onley and
Rugby Riding Club each have two independent auto sources since they host
both their own regular bookings and separately-organised affiliated shows.
Allens Hill Competition Centre has no text-based source of its own (their
site uses image-based show posters) but is genuinely very active on Horse
Monkey (horsemonkey.com) -- see fetch_horsemonkey() in scrapers.py, which
hits Horse Monkey's undocumented internal search API directly. Google does
not index Horse Monkey's content at all, so a plain web search will wrongly
suggest a venue has nothing there; always check the site itself.
HORSEMONKEY_VENUES also picks up a handful of extra clinics/BRC events
Horse Monkey has for Moreton Morrell, Solihull RC, CCR Equestrian, The
Unicorn and Aston Le Walls that their primary sources above don't list --
additive, not a replacement, merged in separately so it can't clobber those
venues' native data if Horse Monkey's scrape fails.
Events tagged "source": "manual" (ASBRC, Crown RC) are left exactly as they
are in the file -- this script never invents or removes those.

If a venue's site is unreachable or its page format changes and nothing can
be parsed, that venue's previous "auto" events are kept as-is rather than
being wiped out, and a warning is printed.
"""

import json
import sys

import requests

from scrapers import (
    TODAY,
    fetch_ics_venue,
    fetch_cotswold_cup,
    fetch_onley_equipe,
    fetch_rugby_entrymaster,
    fetch_horse_events,
    fetch_unicorn_equestrian,
    fetch_lowlands,
    fetch_horsemonkey,
    fetch_walsgrave,
)

DATA_PATH = "data/events.json"

# horse-events.co.uk profile slugs for venues confirmed to have one.
HORSE_EVENTS_VENUES = {
    "swalcliffe": "swalcliffe-park-equestrian",
    "moreton-morrell": "moreton-morrell-college",
    "solihull-rc": "solihull-riding-club",
    "dallas-burston": "dallas-burston-polo-club",
    "barcheston": "barcheston-grounds-farm",
    "aston-le-walls": "aston-le-walls-equestrian",
    "onley": "onley-equestrian-centre",
    "rugby-riding-club": "rugby-riding-club",
}

AUTO_VENUE_KEYS = [
    "moreton-morrell", "swallowfield", "solihull-rc", "walsgrave-arc", "swalcliffe",
    "onley", "rugby-riding-club", "ccr-equestrian", "the-unicorn", "lowlands",
]

# Horse Monkey coverage per venue. Allens Hill has no other source at all;
# the rest already have a primary source above (ICS, their own site, etc.)
# and Horse Monkey just adds a handful of extra clinics/BRC events that
# platform doesn't list -- so these are additive, not a replacement, and
# get merged in separately in main() rather than through AUTO_VENUE_KEYS.
HORSEMONKEY_VENUES = {
    "allens-hill": "Allens Hill",
    "moreton-morrell": "Moreton Morrell",
    "solihull-rc": "Solihull",
    "ccr-equestrian": "CCR",
    "the-unicorn": "Unicorn",
    "aston-le-walls": "Aston Le Walls",
}


def scrape_all(session):
    """Returns {venueKey: events_list or None}. None means the scrape failed
    for that venue and old data for it should be kept as a fallback."""
    results = {key: None for key in AUTO_VENUE_KEYS}

    # NOTE: LocationID=751 on myridinglife.com is NOT Moreton Morrell -- it's
    # "Moreton Equestrian Centre" in Moreton, Dorchester, Dorset (DT2 8RG), a
    # totally unrelated venue that just happens to share the word "Moreton".
    # Only locationID=44 is the real Moreton Morrell (Warwickshire College,
    # Moreton Morrell, CV35 9BL). Do not add 751 back without re-verifying
    # the address on an eventdetails.aspx page -- confirmed by hand 2026-08-10.
    try:
        results["moreton-morrell"] = fetch_ics_venue(
            session, "moreton-morrell",
            "https://www.myridinglife.com/RemoteLocationEventList.aspx?locationID=44&from=rl",
            "https://www.myridinglife.com",
        )
    except Exception as exc:  # noqa: BLE001 -- keep going even if one venue breaks
        print(f"WARN: moreton-morrell scrape failed: {exc}", file=sys.stderr)

    try:
        results["swallowfield"] = fetch_ics_venue(
            session, "swallowfield",
            "https://www.equineaffairs.com/RemoteLocationEventList.aspx?LocationID=1616",
            "https://www.equineaffairs.com",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: swallowfield scrape failed: {exc}", file=sys.stderr)

    try:
        results["solihull-rc"] = fetch_ics_venue(
            session, "solihull-rc",
            "https://www.equineaffairs.com/RemoteLocationEventList.aspx?LocationID=899",
            "https://www.equineaffairs.com",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: solihull-rc scrape failed: {exc}", file=sys.stderr)

    try:
        results["walsgrave-arc"] = fetch_walsgrave(session)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: walsgrave-arc scrape failed: {exc}", file=sys.stderr)

    # NOTE: Onley's real system of record is Equipe, not MyRidingLife -- their
    # myridinglife.com listing (locationID=2291) only had 1 event vs Equipe's
    # ~25. Use fetch_onley_equipe below instead of fetch_ics_venue here.
    try:
        results["onley"] = fetch_onley_equipe(session)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: onley scrape failed: {exc}", file=sys.stderr)

    try:
        results["rugby-riding-club"] = fetch_rugby_entrymaster(session)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: rugby-riding-club scrape failed: {exc}", file=sys.stderr)

    try:
        results["swalcliffe"] = fetch_ics_venue(
            session, "swalcliffe",
            "https://www.equineaffairs.com/RemoteLocationEventList.aspx?LocationID=1093",
            "https://www.equineaffairs.com",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: swalcliffe scrape failed: {exc}", file=sys.stderr)

    try:
        results["ccr-equestrian"] = fetch_ics_venue(
            session, "ccr-equestrian",
            "https://www.myridinglife.com/RemoteLocationEventList.aspx?locationID=2865&from=rl",
            "https://www.myridinglife.com",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: ccr-equestrian scrape failed: {exc}", file=sys.stderr)

    try:
        results["the-unicorn"] = fetch_unicorn_equestrian(session)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: the-unicorn scrape failed: {exc}", file=sys.stderr)

    try:
        results["lowlands"] = fetch_lowlands(session)
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: lowlands scrape failed: {exc}", file=sys.stderr)

    return results


def main():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    manual_events = [e for e in data.get("events", []) if e.get("source") != "auto"]

    # Cotswold Cup and Horse Events entries are tagged by id prefix, not just
    # venueKey, since a single leg/listing can land at a venueKey (e.g.
    # solihull-rc, moreton-morrell) that also has its own independently
    # scraped native events -- keep each source's pool separate so one
    # source's fallback doesn't clobber another's data for the same venue.
    old_auto_by_venue = {}
    old_cotswold_events = []
    old_horse_events_by_venue = {}
    old_horsemonkey_by_venue = {}
    for e in data.get("events", []):
        if e.get("source") != "auto":
            continue
        if e["id"].startswith("cotswold-cup-"):
            old_cotswold_events.append(e)
        elif e["id"].startswith("horse-events-"):
            old_horse_events_by_venue.setdefault(e["venueKey"], []).append(e)
        elif "-hm-" in e["id"]:
            old_horsemonkey_by_venue.setdefault(e["venueKey"], []).append(e)
        else:
            old_auto_by_venue.setdefault(e["venueKey"], []).append(e)

    session = requests.Session()
    scraped = scrape_all(session)

    new_auto_events = []
    for venue_key in AUTO_VENUE_KEYS:
        if scraped[venue_key] is not None:
            new_auto_events.extend(scraped[venue_key])
        else:
            print(f"INFO: keeping previous data for {venue_key} (scrape unavailable)", file=sys.stderr)
            new_auto_events.extend(old_auto_by_venue.get(venue_key, []))

    try:
        new_auto_events.extend(fetch_cotswold_cup(session))
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: cotswold-cup scrape failed: {exc}", file=sys.stderr)
        new_auto_events.extend(old_cotswold_events)

    for venue_key, slug in HORSE_EVENTS_VENUES.items():
        try:
            new_auto_events.extend(fetch_horse_events(session, venue_key, slug))
        except Exception as exc:  # noqa: BLE001
            print(f"WARN: horse-events/{venue_key} scrape failed: {exc}", file=sys.stderr)
            new_auto_events.extend(old_horse_events_by_venue.get(venue_key, []))

    for venue_key, name_filter in HORSEMONKEY_VENUES.items():
        try:
            new_auto_events.extend(fetch_horsemonkey(session, venue_key, name_filter))
        except Exception as exc:  # noqa: BLE001
            print(f"WARN: horsemonkey/{venue_key} scrape failed: {exc}", file=sys.stderr)
            new_auto_events.extend(old_horsemonkey_by_venue.get(venue_key, []))

    new_auto_events.sort(key=lambda e: (e["date"], e["venueKey"], e["title"]))

    data["events"] = manual_events + new_auto_events
    data["updated"] = TODAY.isoformat()

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Wrote {len(data['events'])} events ({len(new_auto_events)} auto, {len(manual_events)} manual)")


if __name__ == "__main__":
    main()
