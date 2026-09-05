// Shared Supabase query for loading a list's venues + events, used by both
// shared.html (a standalone shared calendar) and index.html (Sarah's
// personal app, which additively merges in her own self-serve list -- see
// js/app.js). Kept here once so a fix to this query only has to happen in
// one place.
window.HorseEventsListEvents = (function () {
  "use strict";

  // Throws if the list doesn't exist (or has been deleted) or on a query
  // error -- callers decide how to present that.
  async function fetchListEvents(client, listId) {
    const { data: listVenues, error: lvError } = await client
      .from("list_venues")
      .select("venues(id,name)")
      .eq("list_id", listId);
    if (lvError) throw lvError;
    if (!listVenues || listVenues.length === 0) {
      return null;
    }

    const venues = listVenues.map((row) => ({ key: row.venues.id, name: row.venues.name }));
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

    return { venues, events, updated: latestUpdate ? latestUpdate.slice(0, 10) : null };
  }

  return { fetchListEvents };
})();
