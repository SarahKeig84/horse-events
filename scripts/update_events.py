"""
Refreshes data/events.json from each venue's public booking calendar.

Only touches events tagged "source": "auto" (Moreton Morrell, Swallowfield,
Solihull RC, Walsgrave ARC). Events tagged "source": "manual" (ASBRC, Crown RC)
are left exactly as they are in the file -- this script never invents or
removes those.

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


AUTO_VENUE_KEYS = ["moreton-morrell", "swallowfield", "solihull-rc", "walsgrave-arc"]


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

    return results


def main():
    with open(DATA_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    manual_events = [e for e in data.get("events", []) if e.get("source") != "auto"]
    old_auto_by_venue = {}
    for e in data.get("events", []):
        if e.get("source") == "auto":
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

    new_auto_events.sort(key=lambda e: (e["date"], e["venueKey"], e["title"]))

    data["events"] = manual_events + new_auto_events
    data["updated"] = TODAY.isoformat()

    with open(DATA_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"Wrote {len(data['events'])} events ({len(new_auto_events)} auto, {len(manual_events)} manual)")


if __name__ == "__main__":
    main()
