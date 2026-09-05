// Supabase Edge Function: proxies venue-search/detection sources
// server-side, so neither's request shape has to live in client-side JS and
// neither site sees traffic directly from random visitors' browsers:
//   - Horse Monkey's undocumented internal search API (national, JSON)
//   - horse-events.co.uk's venue directory keyword filter (national, HTML)
//   - four multi-tenant booking platforms without any name search at all
//     (MyRidingLife/EquineAffairs, Equipe, EntryMaster Lite, EC Pro) --
//     detected instead from a venue's own booking-page URL, since there's
//     no way to search them by name (see detectPlatform)
//
// Deploy with: supabase functions deploy search-venues
//
// Request: POST { "query": "<venue name fragment, 3+ chars>" }
//       or POST { "url": "<venue's own booking-page URL>" }
// Response: { "venues": [
//   { "source": "horsemonkey", "name": "<canonical venue_name>", "eventCount": n },
//   { "source": "horse-events", "name": "<venue name>", "slug": "<url slug>", "eventCount"?: n },
//   { "source": "myridinglife" | "equipe" | "entrymaster-lite" | "ecpro",
//     "name": "<best-effort guess>", "externalRef": "<scrape identifier>", "eventCount"?: n }
// ] }
//
// Unlike Horse Monkey's search API (which returns actual event rows, so
// counting them is free), Horse Events' venue directory only returns
// name+slug -- getting a count means a separate fetch of that venue's own
// profile page. Doing that for every match on a broad query (e.g.
// "Equestrian" -> 30+ venues) would make search slow and hammer their site,
// so eventCount is only attached for Horse Events results when a query
// narrows the match list down to MAX_HORSE_EVENTS_COUNT_FETCHES or fewer
// (see attachHorseEventsCounts) -- otherwise it's simply omitted. A `url`
// request only ever names one venue, so its count is always attempted.
//
// Two things learned the hard way while building the personal version of
// this app, both load-bearing here:
//  - Horse Monkey's /uk/search endpoint validates its body strictly:
//    params.currentPage/perPage/sortBy/sortDesc/filter must ALL be present,
//    and filter must be an array of {field, operator, value} objects, or it
//    returns a bare 500 with no useful message. Don't "simplify" this body.
//  - Its `operator` field ("contains", "equals", ...) is not actually
//    enforced server-side -- an "equals" search with a partial value still
//    matched multiple venues in testing, identical to "contains". So this
//    function always treats matches as substring matches and groups by the
//    exact venue_name string returned, rather than trusting `operator` to
//    narrow anything.

const HORSEMONKEY_SEARCH_URL = "https://horsemonkey.com/uk/search";
const HORSE_EVENTS_VENUES_URL = "https://www.horse-events.co.uk/horse-events-venues/";
const HORSE_EVENTS_VENUE_PAGE_URL = "https://www.horse-events.co.uk/venues/";
const MAX_HORSE_EVENTS_COUNT_FETCHES = 12;
const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};
const REQUEST_HEADERS = {
  "User-Agent": "Mozilla/5.0 (compatible; HorseEventsBot/1.0; venue search)",
};

type HorseMonkeyVenue = { source: "horsemonkey"; name: string; eventCount: number };
type HorseEventsVenue = { source: "horse-events"; name: string; slug: string; eventCount?: number };
type UrlSourcedSource = "myridinglife" | "equipe" | "entrymaster-lite" | "ecpro";
type UrlSourcedVenue = {
  source: UrlSourcedSource;
  name: string;
  externalRef: string;
  eventCount?: number;
};

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { headers: CORS_HEADERS });
  }
  if (req.method !== "POST") {
    return json({ error: "Method not allowed" }, 405);
  }

  let body: { query?: unknown; url?: unknown };
  try {
    body = await req.json();
  } catch {
    return json({ error: "Invalid JSON body" }, 400);
  }

  if (typeof body.url === "string") {
    return await handleUrlDetection(body.url);
  }

  const query = body.query;
  if (typeof query !== "string" || query.trim().length < 3) {
    return json({ error: "query must be at least 3 characters" }, 400);
  }
  const trimmedQuery = query.trim().slice(0, 100); // hard cap, ignore anything longer

  // Run both searches concurrently; if one fails, still return the other's
  // results rather than failing the whole search.
  const [horseMonkeyResult, horseEventsResult] = await Promise.allSettled([
    searchHorseMonkey(trimmedQuery),
    searchHorseEvents(trimmedQuery),
  ]);

  const venues: Array<HorseMonkeyVenue | HorseEventsVenue> = [];
  const errors: string[] = [];

  if (horseMonkeyResult.status === "fulfilled") venues.push(...horseMonkeyResult.value);
  else errors.push(`Horse Monkey: ${String(horseMonkeyResult.reason)}`);

  if (horseEventsResult.status === "fulfilled") {
    let horseEventsVenues = horseEventsResult.value;
    if (horseEventsVenues.length > 0 && horseEventsVenues.length <= MAX_HORSE_EVENTS_COUNT_FETCHES) {
      horseEventsVenues = await attachHorseEventsCounts(horseEventsVenues);
    }
    venues.push(...horseEventsVenues);
  } else errors.push(`Horse Events: ${String(horseEventsResult.reason)}`);

  venues.sort((a, b) => a.name.localeCompare(b.name));

  return json({ venues, ...(errors.length ? { partialErrors: errors } : {}) });
});

async function searchHorseMonkey(query: string): Promise<HorseMonkeyVenue[]> {
  const resp = await fetch(HORSEMONKEY_SEARCH_URL, {
    method: "POST",
    headers: {
      ...REQUEST_HEADERS,
      "Content-Type": "application/json",
      "X-Requested-With": "XMLHttpRequest",
    },
    // currentPage/perPage are hardcoded here regardless of what a caller
    // sends -- this function never forwards arbitrary params upstream.
    body: JSON.stringify({
      params: {
        currentPage: 1,
        perPage: 100,
        sortBy: "start",
        sortDesc: false,
        filter: [{ field: "venue_name", operator: "contains", value: query }],
      },
    }),
  });
  if (!resp.ok) throw new Error(`upstream returned ${resp.status}`);
  const data: { rows?: Array<{ venue_name?: string }> } = await resp.json();

  const counts = new Map<string, number>();
  for (const row of data.rows ?? []) {
    const name = (row.venue_name ?? "").trim();
    if (!name) continue;
    counts.set(name, (counts.get(name) ?? 0) + 1);
  }
  return [...counts.entries()].map(([name, eventCount]) => ({
    source: "horsemonkey" as const,
    name,
    eventCount,
  }));
}

async function searchHorseEvents(query: string): Promise<HorseEventsVenue[]> {
  const url = `${HORSE_EVENTS_VENUES_URL}?keyword=${encodeURIComponent(query)}`;
  const resp = await fetch(url, { headers: REQUEST_HEADERS });
  if (!resp.ok) throw new Error(`upstream returned ${resp.status}`);
  const html = await resp.text();

  const seen = new Map<string, string>(); // slug -> name
  const re = /<a href="https:\/\/www\.horse-events\.co\.uk\/venues\/([a-z0-9-]+)"[^>]*>([^<]+)<\/a>/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(html)) !== null) {
    const [, slug, rawName] = match;
    const name = decodeHtmlEntities(rawName).trim();
    if (name) seen.set(slug, name);
  }
  return [...seen.entries()].map(([slug, name]) => ({ source: "horse-events" as const, name, slug }));
}

// Detects which self-serve-able multi-tenant platform a pasted venue URL is
// on and returns the identifier that platform's fetcher needs -- the exact
// same mapping as scripts/scrapers.py's detect_venue_url (kept in sync by
// hand; there's no shared module between Python and Deno here). Deliberately
// does NOT cover Unicorn Equestrian, Walsgrave ARC or the Cotswold Cup --
// those are bespoke one-off parsers tuned to one specific site's own
// markup, not a shared platform, so there's nothing generic to detect.
const EQUIPE_ORGANIZER_RE = /^https?:\/\/entry\.equipe\.com\/organizers\/(\d+)/i;
const MYRIDINGLIFE_HOSTS = new Set([
  "myridinglife.com",
  "www.myridinglife.com",
  "equineaffairs.com",
  "www.equineaffairs.com",
]);

function detectPlatform(url: string): { source: UrlSourcedSource; externalRef: string } | null {
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return null;
  }
  const host = parsed.hostname.toLowerCase();

  if (MYRIDINGLIFE_HOSTS.has(host) && parsed.pathname.toLowerCase().includes("remotelocationeventlist.aspx")) {
    return { source: "myridinglife", externalRef: url };
  }

  const equipeMatch = url.match(EQUIPE_ORGANIZER_RE);
  if (equipeMatch) {
    return { source: "equipe", externalRef: equipeMatch[1] };
  }

  if (host.endsWith(".lite.events")) {
    return { source: "entrymaster-lite", externalRef: `${parsed.protocol}//${parsed.host}` };
  }

  if (host.endsWith(".ecpro.co.uk")) {
    return { source: "ecpro", externalRef: url };
  }

  return null;
}

// Best-effort venue name guess -- these platforms don't expose a clean
// venue-name field the way Horse Monkey and Horse Events do, and where the
// name lives varies enough per platform that one generic <title>-splitting
// heuristic gets it visibly wrong (confirmed live): MyRidingLife/
// EquineAffairs' <title> is "All Events at <venue>"; Equipe's <title> and
// og:title are both platform-generic ("Equipe Entry System Shows"/"Equipe
// Entry") with the real name only in an <h3 class="p-3"> heading;
// EntryMaster Lite's <title> is similarly generic but its og:title meta is
// "<venue> Online Entry System"; EC Pro's <title> is "Upcoming Events |
// <venue>" -- the venue is the LAST segment, not the first. Whatever this
// returns is still just a starting point, not a guarantee -- the visitor
// picking a URL result can see and correct it before adding it.
function guessVenueName(source: UrlSourcedSource, html: string): string | null {
  switch (source) {
    case "myridinglife": {
      const m = html.match(/<title[^>]*>\s*All Events at ([^<]+?)\s*<\/title>/i);
      return m ? decodeHtmlEntities(m[1]).trim() : null;
    }
    case "equipe": {
      const m = html.match(/<h3 class="p-3">\s*([^<]+?)\s*<\/h3>/i);
      return m ? decodeHtmlEntities(m[1]).trim() : null;
    }
    case "entrymaster-lite": {
      const m = html.match(/<meta property="og:title" content="([^"]+)"/i);
      if (!m) return null;
      return decodeHtmlEntities(m[1]).replace(/\s+(online )?entry system\s*$/i, "").trim() || null;
    }
    case "ecpro": {
      const m = html.match(/<title[^>]*>([^<]+)<\/title>/i);
      if (!m) return null;
      const parts = decodeHtmlEntities(m[1]).split("|");
      return parts[parts.length - 1].trim() || null;
    }
  }
}

async function handleUrlDetection(url: string): Promise<Response> {
  const detected = detectPlatform(url.trim());
  if (!detected) {
    return json({ error: "We don't recognize this booking platform yet. Try a Horse Monkey or Horse Events search instead, or check the URL." }, 400);
  }

  let html: string;
  try {
    const resp = await fetch(
      detected.source === "equipe"
        ? `https://entry.equipe.com/organizers/${detected.externalRef}/meetings`
        : detected.externalRef,
      { headers: REQUEST_HEADERS }
    );
    if (!resp.ok) throw new Error(`upstream returned ${resp.status}`);
    html = await resp.text();
  } catch (err) {
    return json({ error: `Couldn't reach that page (${String(err)}). Double-check the URL.` }, 502);
  }

  const venue: UrlSourcedVenue = {
    source: detected.source,
    name: guessVenueName(detected.source, html) || url,
    externalRef: detected.externalRef,
    eventCount: countUpcomingForPlatform(detected.source, html),
  };
  return json({ venues: [venue] });
}

function countUpcomingForPlatform(source: UrlSourcedSource, html: string): number {
  switch (source) {
    case "myridinglife": {
      let count = 0;
      for (const m of html.matchAll(/eventdetails\.aspx\?id=\d+"[^>]*>([^<]+)</g)) {
        if (!/private|group lesson|recall|training field|hayfield|long walk|arena hire/i.test(m[1])) count++;
      }
      return count;
    }
    case "equipe": {
      let count = 0;
      for (const m of html.matchAll(/data-react-component-props-value="([^"]+)"/g)) {
        try {
          const payload = JSON.parse(decodeHtmlEntities(m[1]));
          if (payload.name && payload.url && payload.startsOn && !/cancelled/i.test(payload.name)) count++;
        } catch {
          // skip malformed blobs
        }
      }
      return count;
    }
    case "entrymaster-lite":
      return [...html.matchAll(/<a id="event\d+"><\/a>/g)].length;
    case "ecpro":
      return [...html.matchAll(/class="client-event-listing-link"/g)].length;
  }
}

// Fetches each venue's own profile page in parallel and counts its
// upcoming events. Only called when the match list is small (see
// MAX_HORSE_EVENTS_COUNT_FETCHES) -- a per-venue fetch doesn't scale to
// broad queries. A venue whose page fetch fails is left without an
// eventCount rather than failing the whole search.
async function attachHorseEventsCounts(list: HorseEventsVenue[]): Promise<HorseEventsVenue[]> {
  const pages = await Promise.allSettled(
    list.map((v) =>
      fetch(`${HORSE_EVENTS_VENUE_PAGE_URL}${v.slug}`, { headers: REQUEST_HEADERS }).then((resp) => {
        if (!resp.ok) throw new Error(`upstream returned ${resp.status}`);
        return resp.text();
      })
    )
  );
  return list.map((v, i) => {
    const page = pages[i];
    return page.status === "fulfilled" ? { ...v, eventCount: countHorseEventsUpcoming(page.value) } : v;
  });
}

// Same "result-NNNNN" card pattern scripts/scrapers.py's fetch_horse_events
// parses in full (with date-window filtering) -- here we only need a rough
// upcoming count for display, so cancelled/Pony Club entries are excluded
// but date parsing is skipped (the venue page itself already only lists
// upcoming events).
function countHorseEventsUpcoming(html: string): number {
  let count = 0;
  const blockRe = /data-href="[^"]+"\s+id="result-\d+"(.*?)(?=data-href="[^"]+"\s+id="result-\d+"|$)/gs;
  let match: RegExpExecArray | null;
  while ((match = blockRe.exec(html)) !== null) {
    const titleMatch = match[1].match(/class="result-title overline">([\s\S]*?)<\/div>/);
    if (!titleMatch) continue;
    const rawTitle = titleMatch[1];
    if (/cancelled/i.test(rawTitle)) continue;
    const title = decodeHtmlEntities(rawTitle.replace(/<[^>]+>/g, ""));
    if (/pony club/i.test(title)) continue;
    count++;
  }
  return count;
}

function decodeHtmlEntities(s: string): string {
  return s
    .replace(/&amp;/g, "&")
    .replace(/&#0?39;/g, "'")
    .replace(/&quot;/g, '"')
    .replace(/&lt;/g, "<")
    .replace(/&gt;/g, ">");
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
  });
}
