// Supabase Edge Function: kicks off the existing "update shared calendar
// events" GitHub Actions workflow on demand, via GitHub's workflow_dispatch
// API, instead of only ever running it on its 07:00 UTC schedule
// (.github/workflows/update-shared-events.yml). Called by create.js right
// after a venue is added to a list, so a freshly-added venue's events show
// up within about a minute rather than confusingly not appearing until the
// next scheduled run.
//
// Deliberately does NOT re-implement any scraping logic here -- it just
// triggers the same scripts/update_shared_events.py run that already exists
// and is already tested, so there's only ever one copy of the actual
// per-platform parsing logic to keep working as sites change.
//
// Deploy with: supabase functions deploy trigger-scrape
//
// Requires one Supabase secret (set via `supabase secrets set` or the
// dashboard's Edge Function secrets page), a GitHub token with permission
// to dispatch this one workflow -- NOT the SUPABASE_SERVICE_ROLE_KEY, a
// separate credential:
//   GITHUB_DISPATCH_TOKEN   a fine-grained GitHub personal access token,
//                           scoped to this repo only, with "Actions:
//                           read and write" permission and nothing else.
//
// Request: POST {} (no body needed)
// Response: { "triggered": true } or { "error": "..." }
//
// This is best-effort and safe to call repeatedly -- worst case if it fails
// or is called when nothing new was actually added, the venue's events just
// wait for the next scheduled run instead, exactly like before this existed.

const GITHUB_REPO = "SarahKeig84/horse-events";
const WORKFLOW_FILE = "update-shared-events.yml";
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

  const token = Deno.env.get("GITHUB_DISPATCH_TOKEN");
  if (!token) {
    return json({ error: "GITHUB_DISPATCH_TOKEN is not configured" }, 500);
  }

  try {
    const resp = await fetch(
      `https://api.github.com/repos/${GITHUB_REPO}/actions/workflows/${WORKFLOW_FILE}/dispatches`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${token}`,
          Accept: "application/vnd.github+json",
          "X-GitHub-Api-Version": "2022-11-28",
          "User-Agent": "horse-events-app",
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ ref: "main" }),
      }
    );
    // GitHub returns a bare 204 No Content on success.
    if (!resp.ok) {
      const text = await resp.text();
      return json({ error: `GitHub returned ${resp.status}: ${text}` }, 502);
    }
    return json({ triggered: true });
  } catch (err) {
    return json({ error: String(err) }, 502);
  }
});

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { ...CORS_HEADERS, "Content-Type": "application/json" },
  });
}
