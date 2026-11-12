(function () {
  "use strict";
  // Confirmation avant les actions destructrices : <form data-confirm="...">
  document.addEventListener("submit", (e) => {
    const msg = e.target.dataset.confirm;
    if (msg && !window.confirm(msg)) e.preventDefault();
  });
  // Journal de collecte en direct
  const live = document.getElementById("run-live");
  if (live) {
    const logEl = document.getElementById("run-log");
    const statusEl = document.getElementById("run-status");
    const labels = { queued: "En file", running: "En cours", ok: "Réussie", partial: "Partielle", error: "Échec" };
    const tick = async () => {
      const resp = await fetch(live.dataset.url + "?format=json");
      if (!resp.ok) return;
      const data = await resp.json();
      const atBottom = logEl.scrollHeight - logEl.scrollTop - logEl.clientHeight < 40;
      logEl.textContent = data.log || "En attente d'un worker…";
      if (atBottom) logEl.scrollTop = logEl.scrollHeight;
      statusEl.textContent = labels[data.status] || data.status;
      statusEl.className = "pill " + data.status;
      if (data.status === "queued" || data.status === "running") setTimeout(tick, 2500);
      else window.setTimeout(() => window.location.reload(), 800);
    };
    setTimeout(tick, 1500);
  }
})();
