// Loader for Sarah's personal, static index.html -- all the actual
// rendering logic lives in calendar-core.js (shared with shared.html).
(function () {
  "use strict";

  fetch("data/events.json")
    .then((r) => r.json())
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
