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

  const SOURCE_LABELS = { horsemonkey: "Horse Monkey", "horse-events": "Horse Events" };

  // A venue result is uniquely identified by (source, name) for Horse
  // Monkey or (source, slug) for Horse Events -- two different sources can
  // legitimately return the same venue name, so plain name alone isn't a
  // safe key here the way it was when this only searched one source.
  function resultKey(v) {
    return v.source === "horse-events" ? `horse-events:${v.slug}` : `horsemonkey:${v.name}`;
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
      const subtitle =
        v.source === "horse-events"
          ? SOURCE_LABELS["horse-events"]
          : `${SOURCE_LABELS.horsemonkey} · ${v.eventCount} upcoming event${v.eventCount === 1 ? "" : "s"} found`;
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
      if (data.partialErrors && data.partialErrors.length) {
        showStatus(`Some sources didn't respond (${data.partialErrors.join("; ")}) -- results may be incomplete.`, true);
      }
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
          p_source: v.source,
          p_external_ref: v.source === "horse-events" ? v.slug : null,
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
