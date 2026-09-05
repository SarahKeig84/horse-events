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
    const result = await window.HorseEventsListEvents.fetchListEvents(client, listId);
    if (!result) {
      showError("This calendar doesn't exist (or has been deleted).");
      return;
    }

    const venues = result.venues.map((v, i) => ({ ...v, color: PALETTE[i % PALETTE.length], url: "" }));

    window.HorseEventsCalendar.init({
      updated: result.updated,
      venues,
      events: result.events,
      gaps: [],
    });

    if (ownerToken) {
      const ownerBar = document.getElementById("ownerBar");
      ownerBar.classList.remove("hidden");
      document.getElementById("addVenueLink").href =
        `create.html?list=${encodeURIComponent(listId)}&owner=${encodeURIComponent(ownerToken)}`;
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
