(function () {
  "use strict";

  // A fixed palette assigned by index -- the venues table has no color
  // column (unlike Sarah's personal data/events.json), so shared calendars
  // just cycle through this deterministically based on venue order.
  const PALETTE = [
    "#2563eb", "#16a34a", "#dc2626", "#9333ea", "#ea580c", "#0891b2",
    "#ca8a04", "#db2777", "#78716c", "#4f46e5", "#0369a1", "#65a30d",
    "#d946ef", "#059669", "#c2410c",
  ];

  const params = new URLSearchParams(window.location.search);
  const listId = params.get("list");
  const ownerToken = params.get("owner");

  function showError(message) {
    document.getElementById("mainContent").innerHTML =
      `<p class="empty-state">${window.HorseEventsCalendar.escapeHtml(message)}</p>`;
  }

  if (!listId) {
    showError("No calendar specified. Use the link you were given, or build your own.");
    return;
  }

  let client;
  try {
    client = window.supabase.createClient(window.SUPABASE_URL, window.SUPABASE_ANON_KEY);
  } catch (err) {
    showError(`Could not connect (${err.message}).`);
    return;
  }

  async function load() {
    const { data: listVenues, error: lvError } = await client
      .from("list_venues")
      .select("venues(id,name)")
      .eq("list_id", listId);
    if (lvError) throw lvError;
    if (!listVenues || listVenues.length === 0) {
      showError("This calendar doesn't exist (or has been deleted).");
      return;
    }

    const venues = listVenues.map((row, i) => ({
      key: row.venues.id,
      name: row.venues.name,
      color: PALETTE[i % PALETTE.length],
      url: "",
    }));
    const venueIds = venues.map((v) => v.key);

    const { data: eventRows, error: evError } = await client
      .from("events")
      .select("*")
      .in("venue_id", venueIds);
    if (evError) throw evError;

    const events = (eventRows || []).map((row) => ({
      id: row.id,
      title: row.title,
      venueKey: row.venue_id,
      date: row.date,
      endDate: row.end_date,
      time: row.time,
      type: row.type,
      url: row.url,
      notes: row.notes,
    }));

    const latestUpdate = (eventRows || []).reduce(
      (max, r) => (r.updated_at && r.updated_at > max ? r.updated_at : max),
      ""
    );

    window.HorseEventsCalendar.init({
      updated: latestUpdate ? latestUpdate.slice(0, 10) : null,
      venues,
      events,
      gaps: [],
    });

    if (ownerToken) {
      const ownerBar = document.getElementById("ownerBar");
      ownerBar.classList.remove("hidden");
      document.getElementById("deleteListBtn").addEventListener("click", async () => {
        if (!window.confirm("Delete this calendar? This can't be undone.")) return;
        const { data: deleted, error } = await client.rpc("delete_list_if_owner", {
          p_list_id: listId,
          p_owner_token: ownerToken,
        });
        if (error || !deleted) {
          window.alert("Couldn't delete this calendar. The link may already be invalid.");
          return;
        }
        showError("This calendar has been deleted.");
      });
    }
  }

  load().catch((err) => {
    showError(`Could not load this calendar (${err.message}).`);
  });
})();
