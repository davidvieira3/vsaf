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
  const pickers = cfg.pickers || [];
  const sourceNames = cfg.sourceNames || {};
  // sel[clé] = [{value, label, hint}] ; les valeurs cluster/hôte/datastore sont de la forme "idSource:nom"
  const state = { platform: urlParams.get("platform") || "", q: urlParams.get("q") || "", vm: urlParams.get("vm") || "", sel: {} };

  function describe(key, value) {
    if (key === "source") return { value, label: sourceNames[value] || "#" + value };
    if (key.startsWith("cf_")) return { value, label: value === "(vide)" ? "(non renseigné)" : value };
    const i = value.indexOf(":");
    return { value, label: value.slice(i + 1), hint: sourceNames[value.slice(0, i)] || "" };
  }
  ["source", ...pickers.map((p) => p.key)].forEach((key) => {
    const raw = urlParams.getAll(key).flatMap((v) => (key === "source" ? v.split(",") : [v])).filter(Boolean);
    state.sel[key] = [...new Set(raw)].map((v) => describe(key, v));
  });

  function stateQuery(p = new URLSearchParams()) {
    if (state.platform) p.set("platform", state.platform);
    if (state.q) p.set("q", state.q);
    if (state.vm) p.set("vm", state.vm);
    Object.entries(state.sel).forEach(([key, items]) => items.forEach((it) => p.append(key, it.value)));
    return p;
  }

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
    ajaxURLGenerator: (url, config, params) => {
      const p = stateQuery();
      p.set("page", params.page || 1);
      p.set("size", params.size || 100);
      (params.sort || []).forEach((s, i) => { p.set(`sort[${i}][field]`, s.field); p.set(`sort[${i}][dir]`, s.dir); });
      (params.filter || []).forEach((f, i) => { p.set(`filter[${i}][field]`, f.field); p.set(`filter[${i}][value]`, f.value); });
      return `${url}?${p.toString()}`;
    },
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
    height: pickers.length ? "calc(100vh - 300px)" : "calc(100vh - 230px)",
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
  function refresh() {
    renderChips();
    window.history.replaceState(null, "", `${window.location.pathname}?${stateQuery().toString()}`);
    table.setData();
  }

  document.querySelectorAll("[data-platform]").forEach((btn) => {
    btn.setAttribute("aria-pressed", String(btn.dataset.platform === state.platform));
    btn.addEventListener("click", () => {
      state.platform = btn.dataset.platform;
      document.querySelectorAll("[data-platform]").forEach((b) => b.setAttribute("aria-pressed", String(b === btn)));
      refresh();
    });
  });

  function debounced(input, apply) {
    let timer;
    input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(() => apply(input.value.trim()), 350); });
  }
  const search = document.getElementById("grid-search");
  if (search) { search.value = state.q; debounced(search, (v) => { state.q = v; refresh(); }); }
  const vmInput = document.getElementById("grid-vm");
  if (vmInput) { vmInput.value = state.vm; debounced(vmInput, (v) => { state.vm = v; refresh(); }); }

  // Vues « baies » : simple liste déroulante
  const sourceSel = document.getElementById("grid-source");
  if (sourceSel) {
    if (state.sel.source.length) sourceSel.value = state.sel.source[0].value;
    sourceSel.addEventListener("change", () => {
      state.sel.source = sourceSel.value ? [{ value: sourceSel.value, label: sourceSel.selectedOptions[0].text }] : [];
      refresh();
    });
  }

  // --- Filtres croisés : sélecteurs multiples avec recherche, en cascade -----------
  const pickerBar = document.getElementById("pickers");
  const pickerButtons = {};
  const SCOPED = ["cluster", "host", "datastore"];

  function buttonLabel(def) {
    const n = (state.sel[def.key] || []).length;
    return n ? `${def.label} · ${n}` : def.label;
  }

  function setSelection(key, items) {
    state.sel[key] = items;
    if (key === "source" && items.length) {  // on retire les choix en aval qui n'appartiennent plus aux sources retenues
      const ids = new Set(items.map((i) => i.value));
      SCOPED.forEach((k) => { if (state.sel[k]) state.sel[k] = state.sel[k].filter((i) => ids.has(i.value.split(":")[0])); });
    }
    pickers.forEach((def) => { if (pickerButtons[def.key]) pickerButtons[def.key].textContent = buttonLabel(def); });
    refresh();
  }

  function buildPicker(def) {
    const wrap = document.createElement("div");
    wrap.className = "popover";
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "btn";
    btn.setAttribute("aria-expanded", "false");
    btn.textContent = buttonLabel(def);
    pickerButtons[def.key] = btn;
    const panel = document.createElement("div");
    panel.className = "popover-body picker";
    panel.hidden = true;
    const input = document.createElement("input");
    input.type = "search";
    input.placeholder = "Rechercher…";
    input.setAttribute("aria-label", `Rechercher dans ${def.label}`);
    const list = document.createElement("div");
    list.className = "picker-list";
    panel.append(input, list);
    wrap.append(btn, panel);

    function row(item, checked) {
      const label = document.createElement("label");
      const box = document.createElement("input");
      box.type = "checkbox";
      box.checked = checked;
      box.addEventListener("change", () => {
        const current = (state.sel[def.key] || []).filter((i) => i.value !== item.value);
        setSelection(def.key, box.checked ? [...current, item] : current);
      });
      const text = document.createElement("span");
      text.textContent = item.label;
      label.append(box, text);
      if (item.hint) {
        const hint = document.createElement("span");
        hint.className = "hint";
        hint.textContent = item.hint;
        label.append(hint);
      }
      return label;
    }

    async function load() {
      const p = new URLSearchParams();
      if (state.platform) p.set("platform", state.platform);
      if (def.key !== "source") (state.sel.source || []).forEach((i) => p.append("source", i.value));
      if (def.key === "host") (state.sel.cluster || []).forEach((i) => p.append("cluster", i.value));
      if (input.value.trim()) p.set("q", input.value.trim());
      list.textContent = "Chargement…";
      let options = [];
      try {
        const resp = await fetch(`/api/options/${def.key}/?${p.toString()}`, { headers: { Accept: "application/json" } });
        options = resp.ok ? (await resp.json()).options : [];
      } catch (e) { options = []; }
      const selected = state.sel[def.key] || [];
      const chosen = new Set(selected.map((i) => i.value));
      list.textContent = "";
      selected.forEach((i) => list.append(row(i, true)));
      options.filter((o) => !chosen.has(o.value)).forEach((o) => list.append(row(o, false)));
      if (!list.children.length) list.textContent = "Aucune valeur. Élargissez les filtres placés avant celui-ci.";
      else if (options.length >= 200) {
        const more = document.createElement("p");
        more.className = "hint";
        more.textContent = "200 premières valeurs : tapez quelques lettres pour affiner.";
        list.append(more);
      }
    }

    btn.addEventListener("click", () => {
      const willOpen = panel.hidden;
      document.querySelectorAll(".popover-body.picker").forEach((el) => { el.hidden = true; });
      panel.hidden = !willOpen;
      btn.setAttribute("aria-expanded", String(willOpen));
      if (willOpen) { input.value = ""; load(); input.focus(); }
    });
    debounced(input, load);
    return wrap;
  }

  if (pickerBar) pickers.forEach((def) => pickerBar.append(buildPicker(def)));
  document.addEventListener("click", (e) => {
    if (!e.target.closest(".popover")) document.querySelectorAll(".popover-body.picker").forEach((el) => { el.hidden = true; });
  });

  // Rappel des filtres actifs, chacun retirable d'un clic
  const chipBar = document.getElementById("active-filters");
  function renderChips() {
    if (!chipBar) return;
    chipBar.textContent = "";
    const labels = Object.fromEntries(pickers.map((p) => [p.key, p.label]));
    Object.entries(state.sel).forEach(([key, items]) => {
      if (!labels[key]) return;
      items.forEach((item) => {
        const chip = document.createElement("button");
        chip.type = "button";
        chip.className = "chip";
        chip.title = "Retirer ce filtre";
        chip.textContent = `${labels[key]} : ${item.label}${item.hint ? " (" + item.hint + ")" : ""} ×`;
        chip.addEventListener("click", () => setSelection(key, state.sel[key].filter((i) => i.value !== item.value)));
        chipBar.append(chip);
      });
    });
  }
  renderChips();

  document.getElementById("grid-reset")?.addEventListener("click", () => {
    table.clearHeaderFilter();
    table.clearSort();
    if (search) search.value = "";
    if (vmInput) vmInput.value = "";
    if (sourceSel) sourceSel.value = "";
    state.q = "";
    state.vm = "";
    Object.keys(state.sel).forEach((k) => { state.sel[k] = []; });
    pickers.forEach((def) => { if (pickerButtons[def.key]) pickerButtons[def.key].textContent = buttonLabel(def); });
    refresh();
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
    const p = stateQuery();
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
    let html = "";
    if ((data.links || []).length) {
      html += '<nav class="related" aria-label="Voir aussi">' +
        data.links.map((l) => `<a class="btn small" href="${escapeHtml(l.url)}">${escapeHtml(l.label)}</a>`).join("") + "</nav>";
    }
    html += "<dl>";
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
