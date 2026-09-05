(function () {
  "use strict";

  // Created lazily (not at module load) so a misconfigured Supabase project
  // only breaks "Create my calendar" (which needs it), not the search box
  // above it (which talks to the search-venues edge function directly via
  // fetch() and doesn't need this client at all).
  let client = null;
  function getClient() {
    if (!client) client = window.supabase.createClient(window.SUPABASE_URL, window.SUPABASE_ANON_KEY);
    return client;
  }

  const searchInput = document.getElementById("searchInput");
  const searchBtn = document.getElementById("searchBtn");
  const urlInput = document.getElementById("urlInput");
  const urlBtn = document.getElementById("urlBtn");
  const statusMsg = document.getElementById("statusMsg");
  const resultList = document.getElementById("resultList");
  const selectedBar = document.getElementById("selectedBar");
  const selectedCount = document.getElementById("selectedCount");
  const createBtn = document.getElementById("createBtn");

  const SOURCE_LABELS = {
    horsemonkey: "Horse Monkey",
    "horse-events": "Horse Events",
    myridinglife: "MyRidingLife / EquineAffairs",
    equipe: "Equipe",
    "entrymaster-lite": "EntryMaster Lite",
    ecpro: "EC Pro",
  };

  // If this page was opened with ?list=&owner= (from the "+ Add a venue"
  // link on an existing calendar), venues get added to that list instead of
  // a brand new one being created.
  const params = new URLSearchParams(window.location.search);
  const addToListId = params.get("list");
  const addToOwnerToken = params.get("owner");
  const isAddMode = !!(addToListId && addToOwnerToken);
  if (isAddMode) createBtn.textContent = "Add to my calendar";

  // A venue result is uniquely identified by (source, name) for Horse
  // Monkey, (source, slug) for Horse Events, or (source, externalRef) for
  // everything found by pasting a URL -- two different sources can
  // legitimately return the same venue name, so plain name alone isn't a
  // safe key here the way it was when this only searched one source.
  function resultKey(v) {
    if (v.source === "horse-events") return `horse-events:${v.slug}`;
    if (v.externalRef) return `${v.source}:${v.externalRef}`;
    return `horsemonkey:${v.name}`;
  }

  const selected = new Map();

  function showStatus(text, isError) {
    statusMsg.textContent = text;
    statusMsg.classList.toggle("hidden", !text);
    statusMsg.classList.toggle("error", !!isError);
  }

  function renderResults(venues) {
    resultList.innerHTML = "";
    if (venues.length === 0) {
      showStatus("No matches. Try a shorter or different spelling.", false);
      return;
    }
    showStatus("", false);
    venues.forEach((v) => {
      const key = resultKey(v);
      const row = document.createElement("label");
      row.className = "result-row";
      const checked = selected.has(key);
      // Horse Events only gets an eventCount when the search narrowed to a
      // handful of matches (see attachHorseEventsCounts in the edge
      // function) -- broad queries leave it undefined, so fall back to
      // just the source label rather than printing "undefined".
      const label = SOURCE_LABELS[v.source] || v.source;
      const subtitle =
        v.eventCount == null
          ? label
          : `${label} · ${v.eventCount} upcoming event${v.eventCount === 1 ? "" : "s"} found`;
      row.innerHTML = `
        <input type="checkbox" ${checked ? "checked" : ""} />
        <span>
          <div class="rname"></div>
          <div class="rcount"></div>
        </span>`;
      row.querySelector(".rname").textContent = v.name;
      row.querySelector(".rcount").textContent = subtitle;
      const checkbox = row.querySelector("input");
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) selected.set(key, v);
        else selected.delete(key);
        updateSelectedBar();
      });
      resultList.appendChild(row);
    });
  }

  function updateSelectedBar() {
    const n = selected.size;
    selectedBar.classList.toggle("hidden", n === 0);
    selectedCount.textContent = `${n} venue${n === 1 ? "" : "s"} selected`;
  }

  async function callSearchVenues(body) {
    const resp = await fetch(`${window.SUPABASE_URL}/functions/v1/search-venues`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${window.SUPABASE_ANON_KEY}`,
        apikey: window.SUPABASE_ANON_KEY,
      },
      body: JSON.stringify(body),
    });
    const data = await resp.json();
    if (!resp.ok) throw new Error(data.error || `Search failed (${resp.status})`);
    return data;
  }

  async function runSearch() {
    const query = searchInput.value.trim();
    if (query.length < 3) {
      showStatus("Type at least 3 letters to search.", true);
      return;
    }
    searchBtn.disabled = true;
    showStatus("Searching…", false);
    resultList.innerHTML = "";
    try {
      const data = await callSearchVenues({ query });
      renderResults(data.venues || []);
      if (data.partialErrors && data.partialErrors.length) {
        showStatus(`Some sources didn't respond (${data.partialErrors.join("; ")}) -- results may be incomplete.`, true);
      }
    } catch (err) {
      showStatus(`Search failed: ${err.message}`, true);
    } finally {
      searchBtn.disabled = false;
    }
  }

  async function runUrlDetect() {
    const url = urlInput.value.trim();
    if (!url) {
      showStatus("Paste a venue's booking page URL first.", true);
      return;
    }
    urlBtn.disabled = true;
    showStatus("Checking that URL…", false);
    resultList.innerHTML = "";
    try {
      const data = await callSearchVenues({ url });
      renderResults(data.venues || []);
    } catch (err) {
      showStatus(err.message, true);
    } finally {
      urlBtn.disabled = false;
    }
  }

  function venueRpcArgs(v) {
    // For results found by pasting a URL, externalRef IS the scrape
    // identifier (a listing URL, organizer id, or site URL depending on
    // platform) -- it's stored as canonical_venue_name too, since that's
    // what the unique(source, canonical_venue_name) constraint dedupes
    // repeated adds of the same real-world venue on. `name` stays the
    // (best-effort guessed) display name.
    if (v.source === "horse-events") {
      return { p_name: v.name, p_canonical_venue_name: v.name, p_source: v.source, p_external_ref: v.slug };
    }
    if (v.externalRef) {
      return { p_name: v.name, p_canonical_venue_name: v.externalRef, p_source: v.source, p_external_ref: v.externalRef };
    }
    return { p_name: v.name, p_canonical_venue_name: v.name, p_source: v.source, p_external_ref: null };
  }

  async function createCalendar() {
    if (selected.size === 0) return;
    createBtn.disabled = true;
    showStatus(isAddMode ? "Adding to your calendar…" : "Creating your calendar…", false);
    try {
      const client = getClient();
      const venueIds = [];
      for (const v of selected.values()) {
        const { data: venueId, error } = await client.rpc("get_or_create_venue", venueRpcArgs(v));
        if (error) throw error;
        venueIds.push(venueId);
      }

      if (isAddMode) {
        const { data: added, error: addError } = await client.rpc("add_venues_to_list", {
          p_list_id: addToListId,
          p_owner_token: addToOwnerToken,
          p_venue_ids: venueIds,
        });
        if (addError) throw addError;
        if (!added) throw new Error("This calendar link is no longer valid.");
        window.location.href = `shared.html?list=${encodeURIComponent(addToListId)}&owner=${encodeURIComponent(addToOwnerToken)}`;
        return;
      }

      const { data: rows, error: listError } = await client.rpc("create_list", {
        p_venue_ids: venueIds,
        p_name: null,
      });
      if (listError) throw listError;
      const { id, owner_token } = rows[0];

      window.location.href = `shared.html?list=${encodeURIComponent(id)}&owner=${encodeURIComponent(owner_token)}`;
    } catch (err) {
      showStatus(`Couldn't ${isAddMode ? "add to" : "create"} your calendar: ${err.message}`, true);
      createBtn.disabled = false;
    }
  }

  searchBtn.addEventListener("click", runSearch);
  searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") runSearch();
  });
  urlBtn.addEventListener("click", runUrlDetect);
  urlInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") runUrlDetect();
  });
  createBtn.addEventListener("click", createCalendar);
})();
