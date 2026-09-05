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
  const statusMsg = document.getElementById("statusMsg");
  const resultList = document.getElementById("resultList");
  const selectedBar = document.getElementById("selectedBar");
  const selectedCount = document.getElementById("selectedCount");
  const createBtn = document.getElementById("createBtn");

  // Keyed by venue name (Horse Monkey's canonical venue_name string) ->
  // { name, eventCount }. This IS the exact string we store/re-search by
  // later -- see fetch_horsemonkey's docstring in scripts/scrapers.py for
  // why exact-name tracking matters here.
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
      const row = document.createElement("label");
      row.className = "result-row";
      const checked = selected.has(v.name);
      row.innerHTML = `
        <input type="checkbox" ${checked ? "checked" : ""} />
        <span>
          <div class="rname"></div>
          <div class="rcount">${v.eventCount} upcoming event${v.eventCount === 1 ? "" : "s"} found</div>
        </span>`;
      row.querySelector(".rname").textContent = v.name;
      const checkbox = row.querySelector("input");
      checkbox.addEventListener("change", () => {
        if (checkbox.checked) selected.set(v.name, v);
        else selected.delete(v.name);
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
      const resp = await fetch(`${window.SUPABASE_URL}/functions/v1/search-venues`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${window.SUPABASE_ANON_KEY}`,
          apikey: window.SUPABASE_ANON_KEY,
        },
        body: JSON.stringify({ query }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || `Search failed (${resp.status})`);
      renderResults(data.venues || []);
    } catch (err) {
      showStatus(`Search failed: ${err.message}`, true);
    } finally {
      searchBtn.disabled = false;
    }
  }

  async function createCalendar() {
    if (selected.size === 0) return;
    createBtn.disabled = true;
    showStatus("Creating your calendar…", false);
    try {
      const client = getClient();
      const venueIds = [];
      for (const v of selected.values()) {
        const { data: venueId, error } = await client.rpc("get_or_create_venue", {
          p_name: v.name,
          p_canonical_venue_name: v.name,
          p_source: "horsemonkey",
        });
        if (error) throw error;
        venueIds.push(venueId);
      }

      const { data: rows, error: listError } = await client.rpc("create_list", {
        p_venue_ids: venueIds,
        p_name: null,
      });
      if (listError) throw listError;
      const { id, owner_token } = rows[0];

      window.location.href = `shared.html?list=${encodeURIComponent(id)}&owner=${encodeURIComponent(owner_token)}`;
    } catch (err) {
      showStatus(`Couldn't create your calendar: ${err.message}`, true);
      createBtn.disabled = false;
    }
  }

  searchBtn.addEventListener("click", runSearch);
  searchInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter") runSearch();
  });
  createBtn.addEventListener("click", createCalendar);
})();
