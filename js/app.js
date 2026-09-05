// Loader for Sarah's personal, static index.html -- all the actual
// rendering logic lives in calendar-core.js (shared with shared.html).
(function () {
  "use strict";

  const PALETTE = [
    "#2563eb", "#16a34a", "#dc2626", "#9333ea", "#ea580c", "#0891b2",
    "#ca8a04", "#db2777", "#78716c", "#4f46e5", "#0369a1", "#65a30d",
    "#d946ef", "#059669", "#c2410c",
  ];

  const LIST_STORAGE_KEY = "horseEventsPersonalList";

  // One-time bootstrap: opening index.html?adopt=<listId>:<ownerToken>
  // (a link handed out once, not committed to the repo) saves that pair to
  // this browser's localStorage, then strips it from the address bar so it
  // isn't left sitting in history/bookmarks.
  function adoptFromUrl() {
    const params = new URLSearchParams(window.location.search);
    const adopt = params.get("adopt");
    if (!adopt || !adopt.includes(":")) return;
    const [list, owner] = adopt.split(":");
    if (list && owner) {
      try {
        localStorage.setItem(LIST_STORAGE_KEY, JSON.stringify({ list, owner }));
      } catch (err) {
        // localStorage unavailable (private browsing, storage full) -- the
        // adopt link just won't stick this time, nothing else to do here.
      }
    }
    params.delete("adopt");
    const qs = params.toString();
    window.history.replaceState({}, "", window.location.pathname + (qs ? `?${qs}` : ""));
  }

  function getSavedList() {
    try {
      const raw = localStorage.getItem(LIST_STORAGE_KEY);
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      return parsed && parsed.list && parsed.owner ? parsed : null;
    } catch (err) {
      return null;
    }
  }

  // Fetches Sarah's own self-serve-added venues from Supabase and merges
  // them additively into her static data -- exactly like HORSEMONKEY_VENUES
  // is additive in scripts/update_events.py, just done client-side instead
  // of at scrape time. Never throws: any failure here (offline, Supabase
  // down, no personal list set up yet) just falls back to her existing
  // static venues only, same as before this feature existed.
  async function loadPersonalList(staticData) {
    const saved = getSavedList();
    if (!saved) return staticData;
    try {
      const client = window.supabase.createClient(window.SUPABASE_URL, window.SUPABASE_ANON_KEY);
      const result = await window.HorseEventsListEvents.fetchListEvents(client, saved.list);
      if (!result) return staticData;

      // Personal-list venue ids (UUIDs) can't collide with her static,
      // hand-picked venueKeys, but prefix them anyway for a clear visual
      // distinction if she ever has to debug this.
      const startIndex = staticData.venues.length;
      const keyMap = new Map();
      const venues = result.venues.map((v, i) => {
        const key = `personal-${v.key}`;
        keyMap.set(v.key, key);
        return { key, name: v.name, color: PALETTE[(startIndex + i) % PALETTE.length], url: "" };
      });
      const events = result.events.map((e) => ({ ...e, venueKey: keyMap.get(e.venueKey) || e.venueKey }));

      return {
        updated: staticData.updated,
        venues: [...staticData.venues, ...venues],
        events: [...staticData.events, ...events],
        gaps: staticData.gaps || [],
      };
    } catch (err) {
      console.warn("Could not load personal self-serve venues:", err);
      return staticData;
    }
  }

  function updateAddVenueLink() {
    const saved = getSavedList();
    const bar = document.getElementById("addVenueBar");
    if (!bar) return;
    if (!saved) {
      bar.classList.add("hidden");
      return;
    }
    document.getElementById("addVenueLink").href =
      `create.html?list=${encodeURIComponent(saved.list)}&owner=${encodeURIComponent(saved.owner)}`;
    bar.classList.remove("hidden");
  }

  adoptFromUrl();
  updateAddVenueLink();

  fetch("data/events.json")
    .then((r) => r.json())
    .then((data) => loadPersonalList(data))
    .then(window.HorseEventsCalendar.init)
    .catch((err) => {
      document.getElementById("mainContent").innerHTML =
        '<p class="empty-state">Could not load event data (' +
        window.HorseEventsCalendar.escapeHtml(err.message) +
        ").</p>";
    });

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("service-worker.js").catch(() => {});
    });
  }
})();
