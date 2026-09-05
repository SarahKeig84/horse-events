// Supabase Edge Function: proxies Horse Monkey's undocumented internal
// search API server-side, so its request shape never has to live in
// client-side JS (and so Horse Monkey never sees traffic directly from
// random visitors' browsers).
//
// Deploy with: supabase functions deploy search-venues
//
// Request:  POST { "query": "<venue name fragment, 3+ chars>" }
// Response: { "venues": [{ "name": "<canonical venue_name>", "eventCount": n }] }
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
const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS",
};

Deno.serve(async (req: Request) => {
  if (req.method === "OPTIONS") {
    return new Response(null, { headers: CORS_HEADERS });
  }
  if (req.method !== "POST") {
    return json({ error: "Method not allowed" }, 405);
  }

  let query: unknown;
  try {
    ({ query } = await req.json());
  } catch {
    return json({ error: "Invalid JSON body" }, 400);
  }

  if (typeof query !== "string" || query.trim().length < 3) {
    return json({ error: "query must be at least 3 characters" }, 400);
  }
  const trimmedQuery = query.trim().slice(0, 100); // hard cap, ignore anything longer

  let horseMonkeyResp: Response;
  try {
    horseMonkeyResp = await fetch(HORSEMONKEY_SEARCH_URL, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Requested-With": "XMLHttpRequest",
        "User-Agent": "Mozilla/5.0 (compatible; HorseEventsBot/1.0; venue search)",
      },
      // currentPage/perPage are hardcoded here regardless of what a caller
      // sends -- this function never forwards arbitrary params upstream.
      body: JSON.stringify({
        params: {
          currentPage: 1,
          perPage: 100,
          sortBy: "start",
          sortDesc: false,
          filter: [{ field: "venue_name", operator: "contains", value: trimmedQuery }],
        },
      }),
    });
  } catch (err) {
    return json({ error: `Upstream request failed: ${String(err)}` }, 502);
  }

  if (!horseMonkeyResp.ok) {
    return json({ error: `Upstream returned ${horseMonkeyResp.status}` }, 502);
  }

  let data: { rows?: Array<{ venue_name?: string }> };
  try {
    data = await horseMonkeyResp.json();
  } catch {
    return json({ error: "Upstream returned invalid JSON" }, 502);
  }

  const counts = new Map<string, number>();
  for (const row of data.rows ?? []) {
    const name = (row.venue_name ?? "").trim();
    if (!name) continue;
    counts.set(name, (counts.get(name) ?? 0) + 1);
  }

  const venues = [...counts.entries()]
    .map(([name, eventCount]) => ({ name, eventCount }))
    .sort((a, b) => a.name.localeCompare(b.name));

  return json({ venues });
});

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
  });
}
