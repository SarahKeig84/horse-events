(function () {
  "use strict";

  let DATA = { venues: [], events: [], gaps: [] };
  let hiddenVenues = new Set();
  let currentView = "calendar"; // "calendar" | "list"
  let calMonth; // Date, first-of-month cursor
  let searchTerm = "";

  const venueByKey = () => Object.fromEntries(DATA.venues.map((v) => [v.key, v]));

  function parseISODate(s) {
    const [y, m, d] = s.split("-").map(Number);
    return new Date(y, m - 1, d);
  }
  function isoDate(d) {
    return d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0");
  }
  function sameDay(a, b) {
    return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  }
  function fmtDateLong(d) {
    return d.toLocaleDateString(undefined, { weekday: "long", day: "numeric", month: "long", year: "numeric" });
  }
  function fmtDateShort(d) {
    return d.toLocaleDateString(undefined, { weekday: "short", day: "numeric", month: "short" });
  }

  function matchesFilters(ev) {
    if (hiddenVenues.has(ev.venueKey)) return false;
    if (!searchTerm) return true;
    const venue = venueByKey()[ev.venueKey];
    const hay = [ev.title, venue ? venue.name : "", ev.type, ev.notes].join(" ").toLowerCase();
    return hay.includes(searchTerm);
  }

  function visibleEvents() {
    return DATA.events.filter(matchesFilters).sort((a, b) => a.date.localeCompare(b.date));
  }

  // ---------- Legend ----------
  function renderLegend() {
    const legend = document.getElementById("legend");
    legend.innerHTML = "";
    DATA.venues.forEach((v) => {
      const chip = document.createElement("button");
      chip.className = "legend-chip" + (hiddenVenues.has(v.key) ? " off" : "");
      chip.innerHTML = `<span class="dot" style="background:${v.color}"></span>${v.name}`;
      chip.addEventListener("click", () => {
        if (hiddenVenues.has(v.key)) hiddenVenues.delete(v.key);
        else hiddenVenues.add(v.key);
        renderLegend();
        renderCurrentView();
      });
      legend.appendChild(chip);
    });
  }

  // ---------- Calendar ----------
  function renderCalendar() {
    const grid = document.getElementById("calGrid");
    const label = document.getElementById("monthLabel");
    grid.innerHTML = "";
    label.textContent = calMonth.toLocaleDateString(undefined, { month: "long", year: "numeric" });

    const firstOfMonth = new Date(calMonth.getFullYear(), calMonth.getMonth(), 1);
    const startOffset = (firstOfMonth.getDay() + 6) % 7; // Monday-first
    const gridStart = new Date(firstOfMonth);
    gridStart.setDate(gridStart.getDate() - startOffset);

    const events = visibleEvents();
    const today = new Date();

    for (let i = 0; i < 42; i++) {
      const cellDate = new Date(gridStart);
      cellDate.setDate(gridStart.getDate() + i);
      const dayEvents = events.filter((ev) => sameDay(parseISODate(ev.date), cellDate));

      const cell = document.createElement("div");
      cell.className = "cal-day";
      if (cellDate.getMonth() !== calMonth.getMonth()) cell.classList.add("outside");
      if (sameDay(cellDate, today)) cell.classList.add("today");
      if (dayEvents.length) cell.classList.add("has-events");

      const num = document.createElement("div");
      num.className = "daynum";
      num.textContent = cellDate.getDate();
      cell.appendChild(num);

      if (dayEvents.length) {
        const dots = document.createElement("div");
        dots.className = "cal-dots";
        dayEvents.slice(0, 6).forEach((ev) => {
          const v = venueByKey()[ev.venueKey];
          const dot = document.createElement("span");
          dot.className = "dot";
          dot.style.background = v ? v.color : "#999";
          dots.appendChild(dot);
        });
        cell.appendChild(dots);
        cell.addEventListener("click", () => openDayModal(cellDate, dayEvents));
      }

      grid.appendChild(cell);
    }
  }

  function openDayModal(date, dayEvents) {
    document.getElementById("modalDate").textContent = fmtDateLong(date);
    const container = document.getElementById("modalEvents");
    container.innerHTML = "";
    dayEvents.forEach((ev) => container.appendChild(eventCard(ev)));
    document.getElementById("modalBackdrop").classList.remove("hidden");
  }
  function closeModal() {
    document.getElementById("modalBackdrop").classList.add("hidden");
  }

  // ---------- List ----------
  function eventCard(ev) {
    const v = venueByKey()[ev.venueKey];
    const card = document.createElement("div");
    card.className = "event-card";
    card.style.borderLeftColor = v ? v.color : "#999";
    const timeStr = ev.time ? ` · ${ev.time}` : "";
    const link = ev.url ? `<a href="${ev.url}" target="_blank" rel="noopener">source</a>` : "";
    card.innerHTML = `
      <div>
        <p class="etitle">${escapeHtml(ev.title)}</p>
        <p class="emeta">${v ? escapeHtml(v.name) : "Unknown venue"}${timeStr}${link ? " · " + link : ""}</p>
        ${ev.type ? `<span class="etag">${escapeHtml(ev.type)}</span>` : ""}
      </div>`;
    return card;
  }

  function escapeHtml(s) {
    return String(s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function renderList() {
    const container = document.getElementById("listContainer");
    container.innerHTML = "";
    const events = visibleEvents();
    const groups = new Map();
    events.forEach((ev) => {
      if (!groups.has(ev.date)) groups.set(ev.date, []);
      groups.get(ev.date).push(ev);
    });
    for (const [date, evs] of groups) {
      const group = document.createElement("div");
      group.className = "list-group";
      const h = document.createElement("p");
      h.className = "list-date";
      h.textContent = fmtDateShort(parseISODate(date));
      group.appendChild(h);
      evs.forEach((ev) => group.appendChild(eventCard(ev)));
      container.appendChild(group);
    }
    document.getElementById("emptyState").classList.toggle("hidden", events.length > 0);
  }

  function renderGaps() {
    const list = document.getElementById("gapsList");
    list.innerHTML = "";
    const vbk = venueByKey();
    (DATA.gaps || []).forEach((g) => {
      const li = document.createElement("li");
      const v = vbk[g.venueKey];
      li.textContent = `${v ? v.name : g.venueKey}: ${g.reason}`;
      list.appendChild(li);
    });
    document.getElementById("gapsSection").classList.toggle("hidden", !(DATA.gaps || []).length);
  }

  function renderCurrentView() {
    if (currentView === "calendar") {
      renderCalendar();
      document.getElementById("emptyState").classList.add("hidden");
    } else {
      renderList();
    }
  }

  // ---------- Wiring ----------
  function setView(view) {
    currentView = view;
    document.getElementById("calendarView").classList.toggle("hidden", view !== "calendar");
    document.getElementById("listView").classList.toggle("hidden", view !== "list");
    document.getElementById("calendarViewBtn").classList.toggle("active", view === "calendar");
    document.getElementById("calendarViewBtn").setAttribute("aria-selected", view === "calendar");
    document.getElementById("listViewBtn").classList.toggle("active", view === "list");
    document.getElementById("listViewBtn").setAttribute("aria-selected", view === "list");
    renderCurrentView();
  }

  function init(data) {
    DATA = data;
    calMonth = new Date(new Date().getFullYear(), new Date().getMonth(), 1);

    const updatedLine = document.getElementById("updatedLine");
    if (DATA.updated) updatedLine.textContent = "Last updated " + DATA.updated;

    renderLegend();
    renderGaps();
    setView("calendar");

    document.getElementById("calendarViewBtn").addEventListener("click", () => setView("calendar"));
    document.getElementById("listViewBtn").addEventListener("click", () => setView("list"));
    document.getElementById("prevMonth").addEventListener("click", () => {
      calMonth = new Date(calMonth.getFullYear(), calMonth.getMonth() - 1, 1);
      renderCalendar();
    });
    document.getElementById("nextMonth").addEventListener("click", () => {
      calMonth = new Date(calMonth.getFullYear(), calMonth.getMonth() + 1, 1);
      renderCalendar();
    });
    document.getElementById("searchInput").addEventListener("input", (e) => {
      searchTerm = e.target.value.trim().toLowerCase();
      renderCurrentView();
    });
    document.getElementById("modalClose").addEventListener("click", closeModal);
    document.getElementById("modalBackdrop").addEventListener("click", (e) => {
      if (e.target.id === "modalBackdrop") closeModal();
    });
  }

  fetch("data/events.json")
    .then((r) => r.json())
    .then(init)
    .catch((err) => {
      document.getElementById("mainContent").innerHTML =
        '<p class="empty-state">Could not load event data (' + escapeHtml(err.message) + ").</p>";
    });

  if ("serviceWorker" in navigator) {
    window.addEventListener("load", () => {
      navigator.serviceWorker.register("service-worker.js").catch(() => {});
    });
  }
})();
