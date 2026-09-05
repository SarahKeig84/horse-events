"""
Shared scraping functions, used by both scripts/update_events.py (Sarah's
personal, hardcoded venue list) and scripts/update_shared_events.py (the
dynamic, Supabase-driven venue list for shared/searched calendars).

Each fetch_* function returns a list of event dicts shaped like:
    {"id", "title", "venueKey", "date", "endDate", "time", "type", "url",
     "notes", "source"}
filtered to the [TODAY, WINDOW_END] window. Keeping these here (rather than
duplicated across both orchestrator scripts) means a fix to, say, the Horse
Monkey parser only needs to happen once.
"""

import json
import re
from datetime import datetime, timedelta, date
from html import unescape

import requests
from bs4 import BeautifulSoup

TODAY = datetime.utcnow().date()
WINDOW_END = TODAY + timedelta(days=90)

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; HorseEventsBot/1.0; personal use)"}

# Recurring bookable facility slots that show up in these venues' public
# listings alongside real events -- not things riders want on their calendar.
NOISE_RE = re.compile(
    r"\bprivate\b|\bgroup lesson\b|\brecall\b|\btraining field\b|\bhayfield\b|"
    r"\blong walk\b|\barena hire\b|\bindoor arena hire\b",
    re.IGNORECASE,
)

# Pony Club rallies/championships are restricted to members -- filtered out
# since most riders using this aren't Pony Club members. These are always
# named with "Pony Club" in the title on the sources that carry them.
PONY_CLUB_RE = re.compile(r"pony club", re.IGNORECASE)

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


def fetch_onley_equipe(session):
    """Onley's real system of record is Equipe (organizer id 404), not
    MyRidingLife -- it lists ~25 shows vs MyRidingLife's 1. Each show card
    embeds a clean JSON blob (React component props) with startsOn/endsOn,
    so no free-text date parsing is needed here."""
    resp = session.get("https://entry.equipe.com/organizers/404/meetings", headers=HEADERS, timeout=20)
    resp.raise_for_status()
    html = resp.text

    events = []
    for match in re.finditer(r'data-react-component-props-value="([^"]+)"', html):
        try:
            payload = json.loads(unescape(match.group(1)))
        except ValueError:
            continue
        name = payload.get("name")
        url = payload.get("url")
        starts_on = payload.get("startsOn")
        ends_on = payload.get("endsOn")
        if not name or not url or not starts_on:
            continue
        if "cancelled" in name.lower():
            continue

        title = re.sub(r"\s+", " ", name).strip()
        start = datetime.strptime(starts_on, "%Y-%m-%d").date()
        end = None
        if ends_on and ends_on != starts_on:
            end = datetime.strptime(ends_on, "%Y-%m-%d").date()

        if start < TODAY or start > WINDOW_END:
            continue

        meeting_id = url.rsplit("/", 1)[-1]
        events.append({
            "id": f"onley-equipe-{meeting_id}",
            "title": title,
            "venueKey": "onley",
            "date": start.isoformat(),
            "endDate": end.isoformat() if end else None,
            "time": None,
            "type": classify(title),
            "url": f"https://entry.equipe.com{url}",
            "notes": "",
            "source": "auto",
        })
    return events


def fetch_rugby_entrymaster(session):
    """Rugby Riding Club runs its own EntryMaster Lite site. The homepage
    lists whatever's currently open for booking, including things not at
    their own venue (e.g. an off-site Points Award afternoon) -- only keep
    cards whose own Venue field actually says Rugby Riding Club."""
    resp = session.get("https://rugbyrc.lite.events", headers=HEADERS, timeout=20)
    resp.raise_for_status()
    html = resp.text

    events = []
    for block_match in re.finditer(
        r'<a id="(event\d+)"></a>.*?'
        r'<div class="w3-text-white regularfont">(.*?)</div>\s*'
        r'<div class="w3-text-sand tinyfont"><strong>(.*?)</strong></div>(.*?)'
        r'(?=<a id="event\d+"></a>|\Z)',
        html, re.S,
    ):
        anchor_id, raw_title, raw_date, tail = block_match.groups()
        title = unescape(re.sub(r"<[^>]+>", "", raw_title)).strip()

        venue_match = re.search(r"Venue:</strong>\s*([^<]+)<", tail)
        if not venue_match or "rugby riding club" not in venue_match.group(1).strip().lower():
            continue

        date_str = unescape(re.sub(r"<[^>]+>", "", raw_date)).strip()
        date_tokens = re.findall(r"\d{1,2} [A-Za-z]{3} \d{4}", date_str)
        if not date_tokens:
            continue
        try:
            start = datetime.strptime(date_tokens[0], "%d %b %Y").date()
        except ValueError:
            continue
        end = None
        if len(date_tokens) > 1:
            try:
                end_candidate = datetime.strptime(date_tokens[1], "%d %b %Y").date()
                if end_candidate != start:
                    end = end_candidate
            except ValueError:
                pass

        if start < TODAY or start > WINDOW_END:
            continue

        events.append({
            "id": f"rugby-riding-club-entrymaster-{anchor_id}",
            "title": title,
            "venueKey": "rugby-riding-club",
            "date": start.isoformat(),
            "endDate": end.isoformat() if end else None,
            "time": None,
            "type": classify(title),
            "url": "https://rugbyrc.lite.events",
            "notes": "",
            "source": "auto",
        })
    return events


def fetch_horse_events(session, venue_key, slug):
    """horse-events.co.uk carries British Eventing / Pony Club style events
    that the MyRidingLife/EquineAffairs booking platform doesn't list at
    all. `slug` is that venue's horse-events.co.uk profile slug."""
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


UNICORN_EXCLUDE_RE = re.compile(r"pony club|dog agility", re.IGNORECASE)


def fetch_unicorn_equestrian(session):
    """The Unicorn Equestrian Centre's "What's On" page lists each event as
    an <article> card with a title and a date (or date range). Excludes
    Pony Club (members-only) and dog agility (not a horse event) entries.
    A few cards use non-standard date text (a recurring-series description,
    a slash-separated date with no month name, opening-hours text) and are
    simply skipped since they don't match the "D Month YYYY" pattern -- not
    worth a special-case parser for one or two cards."""
    resp = session.get("https://unicornequestrian.co.uk/whats-on", headers=HEADERS, timeout=20)
    resp.raise_for_status()
    html = resp.text

    events = []
    for block_match in re.finditer(r"<article[^>]*>(.*?)</article>", html, re.S):
        block = block_match.group(1)
        title_match = re.search(r"<h2[^>]*>(.*?)</h2>", block, re.S)
        date_match = re.search(r"lucide-calendar.*?</svg>([^<]+)<", block, re.S)
        if not title_match or not date_match:
            continue

        title = unescape(re.sub(r"<[^>]+>", "", title_match.group(1))).strip()
        if UNICORN_EXCLUDE_RE.search(title):
            continue

        date_str = unescape(date_match.group(1)).strip()
        month_pattern = "|".join(MONTH_NUMBERS)
        date_tokens = re.findall(r"\d{1,2} (?:" + month_pattern + r") \d{4}", date_str)
        if not date_tokens:
            continue
        try:
            start = datetime.strptime(date_tokens[0], "%d %B %Y").date()
        except ValueError:
            continue
        end = None
        if len(date_tokens) > 1:
            try:
                end_candidate = datetime.strptime(date_tokens[1], "%d %B %Y").date()
                if end_candidate > start:  # guards against malformed ranges on their site
                    end = end_candidate
            except ValueError:
                pass

        if start < TODAY or start > WINDOW_END:
            continue

        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        events.append({
            "id": f"the-unicorn-{slug}-{start.isoformat()}",
            "title": title,
            "venueKey": "the-unicorn",
            "date": start.isoformat(),
            "endDate": end.isoformat() if end else None,
            "time": None,
            "type": classify(title),
            "url": "https://unicornequestrian.co.uk/whats-on",
            "notes": "",
            "source": "auto",
        })
    return events


def fetch_lowlands(session):
    """Lowlands Equestrian Centre (an RDA -- Riding for the Disabled
    Association -- centre) runs its own EC Pro booking site. Its upcoming
    events page lists both open unaffiliated shows and recurring RDA-run
    clinics as clean event cards with a DD/MM/YYYY date."""
    resp = session.get("https://lowlandsequestriancentre.ecpro.co.uk/events/upcoming", headers=HEADERS, timeout=20)
    resp.raise_for_status()
    html = resp.text

    events = []
    for match in re.finditer(
        r'<a href="(/events/\d+)" class="client-event-listing-link">[\s\S]*?'
        r'<h2 class="client-event-listing-card-title">(.*?)</h2>[\s\S]*?'
        r'<span>(\d{2}/\d{2}/\d{4})</span>',
        html,
    ):
        href, raw_title, date_str = match.groups()
        title = unescape(re.sub(r"<[^>]+>", "", raw_title)).strip()
        try:
            start = datetime.strptime(date_str, "%d/%m/%Y").date()
        except ValueError:
            continue

        if start < TODAY or start > WINDOW_END:
            continue

        event_id = href.rsplit("/", 1)[-1]
        events.append({
            "id": f"lowlands-{event_id}",
            "title": title,
            "venueKey": "lowlands",
            "date": start.isoformat(),
            "endDate": None,
            "time": None,
            "type": classify(title),
            "url": f"https://lowlandsequestriancentre.ecpro.co.uk{href}",
            "notes": "",
            "source": "auto",
        })
    return events


def fetch_horsemonkey(session, venue_key, venue_filter_value, exact_venue_name=None):
    """Horse Monkey's site is a Vue SPA with a completely undocumented
    internal search API. Reverse-engineered by intercepting the real
    search box's network traffic in a browser -- Google does not index
    this site's content at all, so a plain web search will wrongly suggest
    a venue has "nothing" on Horse Monkey.

    The API is POST https://horsemonkey.com/uk/search with a JSON body
    shaped like:
        {"params": {"currentPage": 1, "perPage": 100, "sortBy": "start",
                     "sortDesc": false,
                     "filter": [{"field": "venue_name", "operator": "contains",
                                 "value": "<venue name>"}]}}
    It's strict: every one of currentPage/perPage/sortBy/sortDesc/filter
    must be present, filter must be an array of {field, operator, value}
    objects, and an unrecognised field/operator crashes with a bare 500
    rather than a helpful validation error. Confirmed working with
    field "venue_name" and operator "contains" -- treat that combination
    as load-bearing and don't "simplify" it without re-testing live.

    IMPORTANT: the `operator` field is NOT actually enforced server-side --
    confirmed live that "equals" with a partial value still matches
    multiple venues, identical to "contains". There is also no stable
    venue id in the response, only the free-text `venue_name` string. So
    when `exact_venue_name` is given (the dynamic/shared-calendar path,
    where a stranger's search could match a generic name shared by more
    than one real venue), rows are additionally filtered in this function
    to exact `venue_name` string equality -- don't rely on the API's
    operator to do that filtering for you.
    """
    body = {
        "params": {
            "currentPage": 1,
            "perPage": 100,
            "sortBy": "start",
            "sortDesc": False,
            "filter": [{"field": "venue_name", "operator": "contains", "value": venue_filter_value}],
        }
    }
    resp = session.post(
        "https://horsemonkey.com/uk/search",
        headers={**HEADERS, "Content-Type": "application/json", "X-Requested-With": "XMLHttpRequest"},
        json=body,
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()

    events = []
    for row in data.get("rows", []):
        title = (row.get("name") or "").strip()
        start_raw = row.get("start")
        if not title or not start_raw:
            continue
        if exact_venue_name is not None and (row.get("venue_name") or "").strip() != exact_venue_name:
            continue
        if NOISE_RE.search(title) or PONY_CLUB_RE.search(title):
            continue
        if (row.get("disciplines") or "") == "Arena Booking":
            continue

        start = datetime.strptime(start_raw[:10], "%Y-%m-%d").date()
        end = None
        end_raw = row.get("end")
        if end_raw:
            end_date = datetime.strptime(end_raw[:10], "%Y-%m-%d").date()
            if end_date > start:
                end = end_date

        if start < TODAY or start > WINDOW_END:
            continue

        events.append({
            "id": f"{venue_key}-hm-{row['id']}",
            "title": title,
            "venueKey": venue_key,
            "date": start.isoformat(),
            "endDate": end.isoformat() if end else None,
            "time": None,
            "type": classify(title),
            "url": row.get("publicUrl") or "https://horsemonkey.com",
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
