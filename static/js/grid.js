/* Grille d'inventaire générique. La configuration (colonnes, seuils) est injectée par le gabarit. */
(function () {
  "use strict";
  const cfgEl = document.getElementById("grid-config");
  if (!cfgEl || typeof Tabulator === "undefined") return;
  const cfg = JSON.parse(cfgEl.textContent);
  const nf1 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 1 });
  const nf2 = new Intl.NumberFormat("fr-FR", { maximumFractionDigits: 2 });
  const nf0 = new Intl.NumberFormat("fr-FR");
  const urlParams = new URLSearchParams(window.location.search);
  const state = { platform: urlParams.get("platform") || "", q: urlParams.get("q") || "", source: urlParams.get("source") || "" };

  function bytes(v) {
    if (v === null || v === undefined || v === "") return "";
    const units = ["o", "Kio", "Mio", "Gio", "Tio", "Pio"];
    let i = 0, n = Number(v);
    while (Math.abs(n) >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return nf1.format(n) + " " + units[i];
  }

  function gauge(value, [warn, crit], suffix) {
    if (value === null || value === undefined) return "";
    const level = value >= crit ? "crit" : value >= warn ? "warn" : "ok";
    const scale = crit * 1.25;
    const w = Math.min(100, (value / scale) * 100);
    const t = Math.min(100, (warn / scale) * 100);
    return `<span class="gauge ${level}" title="Alerte ${nf2.format(warn)}${suffix} · critique ${nf2.format(crit)}${suffix}">` +
      `<b>${nf2.format(value)}${suffix}</b><i style="--w:${w}%;--t:${t}%"></i></span>`;
  }

  const STATUS_CLASS = { ok: "ok", connected: "ok", online: "ok", poweredOn: "ok", running: "ok",
    partial: "warn", queued: "warn", disconnected: "crit", notResponding: "crit", offline: "crit", error: "crit",
    poweredOff: "", stopped: "" };
  const STATUS_LABEL = { ok: "Réussie", partial: "Partielle", error: "Échec", running: "En cours", queued: "En file",
    poweredOn: "Allumée", poweredOff: "Éteinte", suspended: "Suspendue", connected: "Connecté", disconnected: "Déconnecté",
    notResponding: "Ne répond pas", online: "En ligne", offline: "Hors ligne", stopped: "Arrêtée" };

  function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  }

  function formatter(type) {
    switch (type) {
      case "int": return (c) => (c.getValue() === null ? "" : nf0.format(c.getValue()));
      case "float": return (c) => (c.getValue() === null ? "" : nf2.format(c.getValue()));
      case "bytes": return (c) => bytes(c.getValue());
      case "mb": return (c) => bytes(c.getValue() === null ? null : c.getValue() * 1048576);
      case "pct": return (c) => (c.getValue() === null ? "" : nf1.format(c.getValue()) + " %");
      case "cpu_ratio": return (c) => gauge(c.getValue(), cfg.thresholds.cpu, "");
      case "mem_ratio": return (c) => gauge(c.getValue(), cfg.thresholds.mem, "");
      case "ds_pct": return (c) => gauge(c.getValue(), cfg.thresholds.ds, " %");
      case "bool": return (c) => (c.getValue() === true ? "Oui" : c.getValue() === false ? "Non" : "");
      case "datetime": return (c) => (c.getValue() ? new Date(c.getValue()).toLocaleString("fr-FR") : "");
      case "status": return (c) => {
        const v = c.getValue();
        if (!v) return "";
        return `<span class="pill ${STATUS_CLASS[v] || ""}">${escapeHtml(STATUS_LABEL[v] || v)}</span>`;
      };
      case "platform": return (c) => {
        const v = c.getValue();
        return v ? `<span class="tag ${v}">${v === "vmware" ? "VMware" : "Proxmox"}</span>` : "";
      };
      default: return (c) => (c.getValue() === null ? "" : escapeHtml(c.getValue()));
    }
  }

  const NUMERIC = ["int", "float", "bytes", "mb", "pct", "cpu_ratio", "mem_ratio", "ds_pct"];
  const columns = cfg.columns.map((c) => {
    const col = {
      title: c.title, field: c.field, visible: c.visible, frozen: c.frozen, formatter: formatter(c.type),
      headerFilter: "input", headerFilterPlaceholder: NUMERIC.includes(c.type) ? "> 4" : "filtrer",
      hozAlign: NUMERIC.includes(c.type) ? "right" : "left", headerSort: true,
    };
    if (c.width) col.width = c.width;
    if (c.type === "bool") {
      col.headerFilter = "list";
      col.headerFilterParams = { values: { "": "Tous", oui: "Oui", non: "Non" }, clearable: true };
    }
    if (c.type === "datetime") col.headerFilter = false;
    return col;
  });

  const table = new Tabulator("#grid", {
    ajaxURL: `/api/vue/${cfg.key}/`,
    ajaxParams: () => ({ platform: state.platform, q: state.q, source: state.source }),
    pagination: true,
    paginationMode: "remote",
    paginationSize: 100,
    paginationSizeSelector: [50, 100, 250, 500, 1000],
    paginationCounter: (pageSize, currentRow, currentPage, totalRows) =>
      `${nf0.format(totalRows)} ligne${totalRows > 1 ? "s" : ""}`,
    sortMode: "remote",
    filterMode: "remote",
    headerFilterLiveFilterDelay: 450,
    layout: "fitData",
    height: "calc(100vh - 230px)",
    movableColumns: true,
    selectableRows: false,
    columns,
    initialSort: [{ column: cfg.sort, dir: "asc" }],
    persistence: { sort: true, columns: ["width", "visible"] },
    persistenceID: "inv-" + cfg.key,
    placeholder: "Aucun élément ne correspond. Élargissez la recherche ou lancez une collecte.",
    locale: "fr",
    langs: { fr: { pagination: { first: "«", first_title: "Première page", last: "»", last_title: "Dernière page",
      prev: "‹", prev_title: "Page précédente", next: "›", next_title: "Page suivante", page_size: "Lignes" } } },
  });

  table.on("rowClick", (e, row) => openDetail(row.getData().pk));

  // --- Barre d'outils ----------------------------------------------------------
  document.querySelectorAll("[data-platform]").forEach((btn) =>
    btn.addEventListener("click", () => {
      state.platform = btn.dataset.platform;
      document.querySelectorAll("[data-platform]").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
      table.setData();
    }));
  const search = document.getElementById("grid-search");
  let timer;
  search && search.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(() => { state.q = search.value; table.setData(); }, 350);
  });
  const sourceSel = document.getElementById("grid-source");
  if (sourceSel && state.source) sourceSel.value = state.source;
  if (search && state.q) search.value = state.q;
  document.querySelectorAll("[data-platform]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.platform === state.platform)));
  sourceSel && sourceSel.addEventListener("change", () => { state.source = sourceSel.value; table.setData(); });

  document.getElementById("grid-reset")?.addEventListener("click", () => {
    table.clearHeaderFilter();
    table.clearSort();
    if (search) search.value = "";
    state.q = "";
    table.setData();
  });

  // --- Choix des colonnes -----------------------------------------------------
  const colBtn = document.getElementById("columns-btn");
  const colPanel = document.getElementById("columns-panel");
  if (colBtn && colPanel) {
    colBtn.addEventListener("click", () => {
      colPanel.innerHTML = "";
      table.getColumns().forEach((col) => {
        const def = col.getDefinition();
        const label = document.createElement("label");
        const box = document.createElement("input");
        box.type = "checkbox";
        box.checked = col.isVisible();
        box.addEventListener("change", () => (box.checked ? col.show() : col.hide()));
        label.append(box, document.createTextNode(def.title));
        colPanel.append(label);
      });
      colPanel.hidden = !colPanel.hidden;
      colBtn.setAttribute("aria-expanded", String(!colPanel.hidden));
    });
    document.addEventListener("click", (e) => {
      if (!colPanel.hidden && !colPanel.contains(e.target) && e.target !== colBtn) {
        colPanel.hidden = true;
        colBtn.setAttribute("aria-expanded", "false");
      }
    });
  }

  // --- Exports : reprend exactement les filtres et tris affichés -------------
  function currentQuery() {
    const p = new URLSearchParams();
    if (state.platform) p.set("platform", state.platform);
    if (state.q) p.set("q", state.q);
    if (state.source) p.set("source", state.source);
    table.getHeaderFilters().forEach((f, i) => {
      p.set(`filter[${i}][field]`, f.field);
      p.set(`filter[${i}][value]`, f.value);
    });
    table.getSorters().forEach((s, i) => {
      p.set(`sort[${i}][field]`, s.field);
      p.set(`sort[${i}][dir]`, s.dir);
    });
    return p.toString();
  }
  document.querySelectorAll("[data-export]").forEach((a) =>
    a.addEventListener("click", (e) => {
      e.preventDefault();
      window.location = `${a.dataset.export}?${currentQuery()}`;
    }));

  // --- Panneau de détail -------------------------------------------------------
  const drawer = document.getElementById("drawer");
  const drawerTitle = document.getElementById("drawer-title");
  const drawerBody = document.getElementById("drawer-body");
  let lastFocus = null;

  function renderValue(v) {
    if (v === null || v === undefined || v === "") return '<span class="hint">—</span>';
    if (typeof v === "boolean") return v ? "Oui" : "Non";
    if (typeof v === "object") return `<pre>${escapeHtml(JSON.stringify(v, null, 2))}</pre>`;
    if (typeof v === "string" && /^https:\/\/[^\s"<>]+$/.test(v)) {
      return `<a href="${escapeHtml(v)}" target="_blank" rel="noopener">${escapeHtml(v)}</a>`;
    }
    return escapeHtml(v);
  }

  async function openDetail(pk) {
    lastFocus = document.activeElement;
    drawerTitle.textContent = "Chargement…";
    drawerBody.innerHTML = "";
    drawer.classList.add("open");
    drawer.setAttribute("aria-hidden", "false");
    drawer.querySelector("button").focus();
    const resp = await fetch(`/api/vue/${cfg.key}/${pk}/`, { headers: { Accept: "application/json" } });
    if (!resp.ok) { drawerTitle.textContent = "Élément introuvable (supprimé par une collecte plus récente ?)"; return; }
    const data = await resp.json();
    drawerTitle.textContent = data.title;
    let html = "<dl>";
    for (const [k, v] of Object.entries(data.fields)) html += `<dt>${escapeHtml(k)}</dt><dd>${renderValue(v)}</dd>`;
    html += "</dl>";
    const extra = data.extra || {};
    for (const [k, v] of Object.entries(extra)) {
      html += `<h3>${escapeHtml(k)}</h3>`;
      if (Array.isArray(v) && v.length && typeof v[0] === "object" && v[0] !== null) {
        const keys = [...new Set(v.flatMap((o) => Object.keys(o)))];
        html += '<div class="table-wrap"><table class="plain"><thead><tr>' + keys.map((x) => `<th>${escapeHtml(x)}</th>`).join("") +
          "</tr></thead><tbody>" + v.map((o) => "<tr>" + keys.map((x) => `<td>${renderValue(o[x])}</td>`).join("") + "</tr>").join("") +
          "</tbody></table></div>";
      } else {
        html += renderValue(v);
      }
    }
    drawerBody.innerHTML = html;
  }

  function closeDetail() {
    drawer.classList.remove("open");
    drawer.setAttribute("aria-hidden", "true");
    if (lastFocus) lastFocus.focus();
  }
  document.getElementById("drawer-close")?.addEventListener("click", closeDetail);
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && drawer.classList.contains("open")) closeDetail(); });
})();
