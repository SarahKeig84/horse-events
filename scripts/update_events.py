"""
Refreshes data/events.json from each venue's public booking calendar.

Only touches events tagged "source": "auto": Moreton Morrell, Swallowfield,
Solihull RC, Walsgrave ARC and Swalcliffe Park (native booking-platform
events), Cotswold Cup legs at those venues plus Offchurch Bury/Hazleton
Manor/Waverton House/Cirencester Park, and horse-events.co.uk listings at
Swalcliffe, Moreton Morrell, Solihull RC, Dallas Burston, Barcheston, Aston
Le Walls, Onley, and Rugby Riding Club (Pony Club-restricted events are
filtered out of the horse-events.co.uk listings -- Sarah isn't a member).
Events tagged "source": "manual" (ASBRC, Crown RC) are left exactly as they
are in the file -- this script never invents or removes those.

If a venue's site is unreachable or its page format changes and nothing can
be parsed, that venue's previous "auto" events are kept as-is rather than
being wiped out, and a warning is printed.
"""

import json
import re
import sys
from datetime import datetime, timedelta, date
from html import unescape

import requests
from bs4 import BeautifulSoup

DATA_PATH = "data/events.json"
TODAY = datetime.utcnow().date()
WINDOW_END = TODAY + timedelta(days=90)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; HorseEventsBot/1.0; personal use)"}

# Recurring bookable facility slots that show up in these venues' public
# listings alongside real events -- not things Sarah wants on her calendar.
NOISE_RE = re.compile(
    r"\bprivate\b|\bgroup lesson\b|\brecall\b|\btraining field\b|\bhayfield\b|"
    r"\blong walk\b|\barena hire\b|\bindoor arena hire\b",
    re.IGNORECASE,
)

TYPE_RULES = [
    (re.compile(r"clinic", re.IGNORECASE), "Clinic"),
    (re.compile(r"dressage", re.IGNORECASE), "Dressage"),
    (re.compile(r"show ?jump", re.IGNORECASE), "Showjumping"),
    (re.compile(r"championship|\bshow\b", re.IGNORECASE), "Show"),
    (re.compile(r"training|schooling", re.IGNORECASE), "Training"),
    (re.compile(r"unaffiliated", re.IGNORECASE), "Unaffiliated"),
]


def classify(title):
    for pattern, label in TYPE_RULES:
        if pattern.search(title):
            return label
    return "Other"


def fetch_ics_venue(session, venue_key, listing_url, site_root):
    """Scrape a MyRidingLife/EquineAffairs-style listing page: pull every
    event id + title off the page, drop noise, then fetch each event's own
    ICS file for a clean structured date."""
    resp = session.get(listing_url, headers=HEADERS, timeout=20)
    resp.raise_for_status()
    html = resp.text

    seen = {}
    for match in re.finditer(r'eventdetails\.aspx\?id=(\d+)"[^>]*>([^<]+)<', html):
        eid, raw_title = match.group(1), match.group(2)
        seen[eid] = unescape(raw_title).strip()

    events = []
    for eid, title in seen.items():
        if NOISE_RE.search(title):
            continue
        try:
            ics_resp = session.get(f"{site_root}/GenerateICS.aspx?id={eid}", headers=HEADERS, timeout=20)
            ics_resp.raise_for_status()
            ics = ics_resp.text
        except requests.RequestException:
            continue

        start_match = re.search(r"DTSTART;VALUE=DATE:(\d{8})", ics)
        if not start_match:
            continue
        start = datetime.strptime(start_match.group(1), "%Y%m%d").date()

        end = None
        end_match = re.search(r"DTEND;VALUE=DATE:(\d{8})", ics)
        if end_match:
            end_exclusive = datetime.strptime(end_match.group(1), "%Y%m%d").date()
            last_day = end_exclusive - timedelta(days=1)  # ICS DTEND is exclusive
            if last_day > start:
                end = last_day

        if start < TODAY or start > WINDOW_END:
            continue

        events.append({
            "id": f"{venue_key}-{eid}",
            "title": title,
            "venueKey": venue_key,
            "date": start.isoformat(),
            "endDate": end.isoformat() if end else None,
            "time": None,
            "type": classify(title),
            "url": f"{site_root}/eventdetails.aspx?id={eid}",
            "notes": "",
            "source": "auto",
        })
    return events


MONTH_NUMBERS = {
    name: i for i, name in enumerate(
        ["January", "February", "March", "April", "May", "June", "July", "August",
         "September", "October", "November", "December"], start=1)
}

# Only legs close to CV35, or anywhere in Gloucestershire, per Sarah's request --
# most of the series (Dorset, Derbyshire, Somerset, Wiltshire, West Sussex) is
# too far away to be worth showing. Matched against the leg's venue name.
COTSWOLD_CUP_VENUE_MAP = {
    "offchurch": "offchurch-bury",
    "solihull": "solihull-rc",
    "moreton morrell": "moreton-morrell",
    "hazleton": "hazleton-manor",
    "waverton": "waverton-house",
    "cirencester": "cirencester-park",
}


def fetch_cotswold_cup(session):
    """The Cotswold Cup is a traveling unaffiliated eventing series, not a
    single venue -- its site (cotswoldcup.co.uk) publishes one central
    calendar of legs as a series of <h4>name</h4><p>location</p><p>date</p>
    blocks. We only keep legs at venues in COTSWOLD_CUP_VENUE_MAP."""
    resp = session.get("https://cotswoldcup.co.uk/", headers=HEADERS, timeout=20)
    resp.raise_for_status()
    html = resp.text

    events = []
    for block in re.finditer(
        r"<h4>(.*?)</h4>\s*<p>(.*?)</p>\s*<p>(.*?)</p>(.*?)(?=<h4>|\Z)", html, re.S
    ):
        raw_name, _location, raw_date, tail = block.groups()
        name = re.sub(r"\*\s*NEW\s*\*", "", unescape(raw_name)).strip()

        venue_key = None
        for needle, key in COTSWOLD_CUP_VENUE_MAP.items():
            if needle in name.lower():
                venue_key = key
                break
        if venue_key is None:
            continue

        if "CANCELLED" in tail[:400]:
            continue

        date_str = unescape(raw_date)
        month_match = re.search(
            r"(" + "|".join(MONTH_NUMBERS) + r")\s+(\d{4})", date_str, re.IGNORECASE
        )
        if not month_match:
            continue
        month_name, year = month_match.group(1), int(month_match.group(2))
        month_num = MONTH_NUMBERS[month_name.title()]
        days = [int(d) for d in re.findall(r"\d{1,2}", date_str.split(month_name)[0])]
        if not days:
            continue
        start = date(year, month_num, min(days))
        end = date(year, month_num, max(days)) if max(days) != min(days) else None

        if start < TODAY or start > WINDOW_END:
            continue

        title = "Cotswold Cup Championships" if "champs" in name.lower() else "Cotswold Cup Qualifier"
        events.append({
            "id": f"cotswold-cup-{venue_key}-{start.isoformat()}",
            "title": title,
            "venueKey": venue_key,
            "date": start.isoformat(),
            "endDate": end.isoformat() if end else None,
            "time": None,
            "type": "Show",
            "url": "https://cotswoldcup.co.uk/",
            "notes": "",
            "source": "auto",
        })
    return events


# horse-events.co.uk profile slugs for venues confirmed to have one. This site
# carries British Eventing / Pony Club style events that the MyRidingLife /
# EquineAffairs booking platform doesn't list at all, so it's genuinely
# additional coverage rather than a duplicate of the ICS venues.
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

# Sarah isn't a Pony Club member, so Pony Club rallies/championships (which
# are restricted to members) aren't things she can actually attend -- these
# are always named with "Pony Club" in the title on horse-events.co.uk.
PONY_CLUB_RE = re.compile(r"pony club", re.IGNORECASE)


def fetch_horse_events(session, venue_key, slug):
    resp = session.get(f"https://www.horse-events.co.uk/venues/{slug}", headers=HEADERS, timeout=20)
    resp.raise_for_status()
    html = resp.text

    events = []
    for block_match in re.finditer(
        r'data-href="([^"]+)"\s+id="result-(\d+)"(.*?)(?=data-href="[^"]+"\s+id="result-\d+"|\Z)',
        html, re.S,
    ):
        href, result_id, block = block_match.groups()

        title_match = re.search(r'class="result-title overline">(.*?)</div>', block, re.S)
        date_match = re.search(r'class="result-date">(.*?)</div>', block, re.S)
        if not title_match or not date_match:
            continue

        raw_title = title_match.group(1)
        cancelled = bool(re.search(r"cancelled", raw_title, re.IGNORECASE))
        title = re.sub(r"<[^>]+>", "", raw_title)
        title = re.sub(r"\s*-\s*Cancelled\s*$", "", unescape(title), flags=re.IGNORECASE).strip()
        if cancelled:
            continue
        if PONY_CLUB_RE.search(title):
            continue

        date_str = unescape(re.sub(r"<[^>]+>", "", date_match.group(1))).strip()
        month_match = re.search(
            r"(" + "|".join(MONTH_NUMBERS) + r")\s+(\d{4})", date_str, re.IGNORECASE
        )
        if not month_match:
            continue
        month_name, year = month_match.group(1), int(month_match.group(2))
        month_num = MONTH_NUMBERS[month_name.title()]
        days = [int(d) for d in re.findall(r"\d{1,2}", date_str.split(month_name)[0])]
        if not days:
            continue
        start = date(year, month_num, min(days))
        end = date(year, month_num, max(days)) if max(days) != min(days) else None

        if start < TODAY or start > WINDOW_END:
            continue

        events.append({
            "id": f"horse-events-{venue_key}-{result_id}",
            "title": title,
            "venueKey": venue_key,
            "date": start.isoformat(),
            "endDate": end.isoformat() if end else None,
            "time": None,
            "type": classify(title),
            "url": href,
            "notes": "",
            "source": "auto",
        })
    return events


def fetch_walsgrave(session):
    """Walsgrave ARC publishes a plain-text 'Show Dates <year>' list on their
    calendar page rather than a booking system -- parse that section only."""
    resp = session.get("https://www.walsgravearc.co.uk/calendar", headers=HEADERS, timeout=20)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")
    text = soup.get_text("\n")

    section_match = re.search(r"Show Dates (\d{4})(.*?)Download", text, re.S)
    if not section_match:
        return []
    year = int(section_match.group(1))
    section = section_match.group(2)

    events = []
    for line_match in re.finditer(
        r"([A-Za-z]+)\s*(\d{1,2})(?:st|nd|rd|th)?\s*[–—-]\s*([^\n]+)", section
    ):
        month_name, day, desc = line_match.groups()
        try:
            event_date = datetime.strptime(f"{month_name} {int(day)} {year}", "%B %d %Y").date()
        except ValueError:
            continue
        if event_date < TODAY or event_date > WINDOW_END:
            continue
        desc = desc.strip()
        events.append({
            "id": f"walsgrave-arc-{event_date.isoformat()}",
            "title": desc,
            "venueKey": "walsgrave-arc",
            "date": event_date.isoformat(),
            "endDate": None,
            "time": "10:00",
            "type": classify(desc),
            "url": "https://www.walsgravearc.co.uk/calendar",
            "notes": "",
            "source": "auto",
        })
    return events


AUTO_VENUE_KEYS = ["moreton-morrell", "swallowfield", "solihull-rc", "walsgrave-arc", "swalcliffe"]


def scrape_all(session):
    """Returns {venueKey: events_list or None}. None means the scrape failed
    for that venue and old data for it should be kept as a fallback."""
    results = {key: None for key in AUTO_VENUE_KEYS}

    try:
        mm = fetch_ics_venue(
            session, "moreton-morrell",
            "https://www.myridinglife.com/RemoteLocationEventList.aspx?LocationID=751",
            "https://www.myridinglife.com",
        )
        mm += fetch_ics_venue(
            session, "moreton-morrell",
            "https://www.myridinglife.com/RemoteLocationEventList.aspx?locationID=44&from=rl",
            "https://www.myridinglife.com",
        )
        results["moreton-morrell"] = mm
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

    try:
        results["swalcliffe"] = fetch_ics_venue(
            session, "swalcliffe",
            "https://www.equineaffairs.com/RemoteLocationEventList.aspx?LocationID=1093",
            "https://www.equineaffairs.com",
        )
    except Exception as exc:  # noqa: BLE001
        print(f"WARN: swalcliffe scrape failed: {exc}", file=sys.stderr)

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
    for e in data.get("events", []):
        if e.get("source") != "auto":
            continue
        if e["id"].startswith("cotswold-cup-"):
            old_cotswold_events.append(e)
        elif e["id"].startswith("horse-events-"):
            old_horse_events_by_venue.setdefault(e["venueKey"], []).append(e)
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

    new_auto_events.sort(key=lambda e: (e["date"], e["venueKey"], e["title"]))

    data["events"] = manual_events + new_auto_events
    data["updated"] = TODAY.isoformat()

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Wrote {len(data['events'])} events ({len(new_auto_events)} auto, {len(manual_events)} manual)")


if __name__ == "__main__":
    main()
