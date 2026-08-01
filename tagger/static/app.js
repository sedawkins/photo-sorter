/* Photo Sorter — SPA frontend (Phase 1: Date + Location browse views) */

// ── Passcode gate ─────────────────────────────────────────────────────────────
(function() {
  const CODE = "Dawkins0409";
  if (sessionStorage.getItem("auth") !== CODE) {
    const entry = prompt("Enter passcode to view family photos:");
    if (entry !== CODE) {
      document.body.innerHTML = `<div style="font-family:sans-serif;display:flex;align-items:center;justify-content:center;height:100vh;margin:0;color:#555;font-size:15px;">Incorrect passcode. Please try again.</div>`;
      throw new Error("auth");
    }
    sessionStorage.setItem("auth", CODE);
  }
})();

const API_KEY = window.PHOTO_API_KEY || "";
const API_BASE = window.API_BASE || "";  // e.g. "https://photo-sorter-vm.westus2.cloudapp.azure.com:8000"

// ── API helpers ───────────────────────────────────────────────────────────────

async function api(path, options = {}) {
  const headers = {
    ...(API_KEY ? { "X-API-Key": API_KEY } : {}),
    ...(options.body ? { "Content-Type": "application/json" } : {}),
  };
  const resp = await fetch(API_BASE + path, { ...options, headers });
  if (!resp.ok) throw new Error(`API error ${resp.status} for ${path}`);
  return resp.json();
}

function thumbUrl(photoPath) {
  return API_BASE + "/api/thumb?path=" + encodeURIComponent(photoPath);
}

// ── State ─────────────────────────────────────────────────────────────────────

let currentView = "date";    // "date" | "location" | "search"
let dateStack = [];          // breadcrumb: [] | [year] | [year, month]
let locationStack = [];      // [] | [country, city] | [country, city, year, month]
let searchState = { q: "", offset: 0, total: 0 };

// ── Nav ───────────────────────────────────────────────────────────────────────

function showView(view) {
  currentView = view;
  document.querySelectorAll(".view").forEach(el => el.classList.remove("active"));
  document.getElementById("view-" + view).classList.add("active");

  const back = document.getElementById("nav-back");
  if (view === "home") {
    back.innerHTML = "";
  } else {
    back.innerHTML = `<a class="nav-back-link" onclick="showView('home')">← Home</a>`;
  }

  if (view === "date" && dateStack.length === 0) loadYears();
  if (view === "location" && locationStack.length === 0) loadLocations();
  if (view === "tag") loadClumps();
  if (view === "search") setTimeout(() => document.getElementById("search-input").focus(), 50);
}

// ── Stats hero ────────────────────────────────────────────────────────────────

async function loadStats() {
  try {
    const s = await api("/api/stats");
    document.getElementById("stat-organized").textContent = s.organized.toLocaleString();
    document.getElementById("stat-with-location").textContent = s.with_location.toLocaleString();
    document.getElementById("stat-date-only").textContent = s.date_only.toLocaleString();
    document.getElementById("stat-movies").textContent = s.movies.toLocaleString();
    document.getElementById("stat-duplicates").textContent = s.duplicates_skipped.toLocaleString();
  } catch (e) {
    console.warn("Stats unavailable (VM may be offline):", e.message);
  }
}

// ── Date view ─────────────────────────────────────────────────────────────────

async function loadYears() {
  dateStack = [];
  const el = document.getElementById("date-content");
  el.innerHTML = `<div class="empty"><span class="spinner"></span>Loading years…</div>`;
  updateDateHeader();
  try {
    const years = await api("/api/years");
    if (!years.length) { el.innerHTML = `<div class="empty">No photos found.</div>`; return; }
    el.innerHTML = `<div class="card-grid">${years.map(y => `
      <div class="card" onclick="loadMonths('${y.year}')">
        <div class="card-title">${y.year}</div>
        <div class="card-count">${y.count.toLocaleString()} photos</div>
      </div>`).join("")}</div>`;
  } catch (e) {
    el.innerHTML = `<div class="empty">Could not load years. Is the VM running?</div>`;
  }
}

async function loadMonths(year) {
  dateStack = [year];
  const el = document.getElementById("date-content");
  el.innerHTML = `<div class="empty"><span class="spinner"></span>Loading ${year}…</div>`;
  updateDateHeader();
  try {
    const months = await api(`/api/years/${year}/months`);
    if (!months.length) { el.innerHTML = `<div class="empty">No photos for ${year}.</div>`; return; }
    el.innerHTML = `<div class="card-grid">${months.map(m => `
      <div class="card" onclick="loadPhotosByMonth('${year}','${m.month}')">
        <div class="card-title">${m.month}</div>
        <div class="card-count">${m.count.toLocaleString()} photos</div>
      </div>`).join("")}</div>`;
  } catch (e) {
    el.innerHTML = `<div class="empty">Could not load months.</div>`;
  }
}

async function loadPhotosByMonth(year, month) {
  dateStack = [year, month];
  const el = document.getElementById("date-content");
  el.innerHTML = `<div class="empty"><span class="spinner"></span>Loading ${month} ${year}…</div>`;
  updateDateHeader();
  try {
    const photos = await api(`/api/years/${year}/months/${month}/photos`);
    el.innerHTML = renderPhotoGroups(photos);
    lazyLoadThumbs(el);
  } catch (e) {
    el.innerHTML = `<div class="empty">Could not load photos.</div>`;
  }
}

function updateDateHeader() {
  const el = document.getElementById("date-header");
  if (dateStack.length === 0) {
    el.innerHTML = `<span class="section-title">Browse by Date</span>`;
  } else if (dateStack.length === 1) {
    el.innerHTML = `
      <a class="back-link" onclick="loadYears()">← All years</a>
      <span class="section-title">${dateStack[0]}</span>`;
  } else {
    el.innerHTML = `
      <a class="back-link" onclick="loadYears()">← All years</a>
      <a class="back-link" onclick="loadMonths('${dateStack[0]}')">${dateStack[0]}</a>
      <span class="section-title">${dateStack[1]} ${dateStack[0]}</span>`;
  }
}

// ── Location view ─────────────────────────────────────────────────────────────

let _map = null;

async function loadLocations() {
  locationStack = [];
  const el = document.getElementById("location-content");
  const mapEl = document.getElementById("location-map");
  mapEl.style.display = "block";
  el.innerHTML = `<div class="empty"><span class="spinner"></span>Loading locations…</div>`;
  updateLocationHeader();
  try {
    const locs = await api("/api/locations");
    if (!locs.length) { el.innerHTML = `<div class="empty">No geotagged photos found.</div>`; return; }

    // ── Leaflet map ──────────────────────────────────────────────────────────
    const mapped = locs.filter(l => l.lat && l.lon);
    if (mapped.length) {
      if (_map) { _map.remove(); _map = null; }
      _map = L.map("location-map", {
        zoomControl: true,
        worldCopyJump: false,
        maxBounds: [[-90, -180], [90, 180]],
        maxBoundsViscosity: 1.0,
      });
      L.tileLayer("https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png", {
        attribution: "© OpenStreetMap © CARTO",
        maxZoom: 14,
        noWrap: true,
      }).addTo(_map);

      const maxCount = Math.max(...mapped.map(l => l.count));
      const markers = mapped.map(l => {
        const r = 8 + 34 * Math.sqrt(l.count / maxCount);
        const circle = L.circleMarker([l.lat, l.lon], {
          radius: r,
          fillColor: "#0070f3",
          fillOpacity: 0.55,
          color: "#0070f3",
          weight: 1.5,
        }).addTo(_map);
        const sub = l.country === "US" ? l.state_or_region : l.country;
        circle.bindTooltip(`<strong>${l.city}</strong><br>${sub}<br>${l.count.toLocaleString()} photos`);
        circle.on("click", () => loadLocationYears(l.country, l.city));
        return circle;
      });

      const group = L.featureGroup(markers);
      _map.fitBounds(group.getBounds().pad(0.15));
    } else {
      mapEl.style.display = "none";
    }

    // ── List below map ───────────────────────────────────────────────────────
    el.innerHTML = `<div class="location-list">${locs.map(l => {
      const sub = l.country === "US" ? l.state_or_region : l.country;
      return `<div class="location-row" onclick="loadLocationYears('${esc(l.country)}','${esc(l.city)}')">
        <div>
          <div class="location-name">${l.city}</div>
          <div class="location-sub">${sub || l.country}</div>
        </div>
        <div class="location-count">${l.count.toLocaleString()}</div>
      </div>`;
    }).join("")}</div>`;
  } catch (e) {
    mapEl.style.display = "none";
    el.innerHTML = `<div class="empty">Could not load locations. Is the VM running?</div>`;
  }
}

async function loadLocationYears(country, city) {
  locationStack = [country, city];
  document.getElementById("location-map").style.display = "none";
  const el = document.getElementById("location-content");
  el.innerHTML = `<div class="empty"><span class="spinner"></span>Loading ${city}…</div>`;
  updateLocationHeader();
  try {
    const years = await api(`/api/locations/${encodeURIComponent(country)}/${encodeURIComponent(city)}/years`);
    // Group by year
    const byYear = {};
    years.forEach(r => {
      if (!byYear[r.year]) byYear[r.year] = [];
      byYear[r.year].push(r);
    });
    const yearsSorted = Object.keys(byYear).sort((a, b) => b - a);
    el.innerHTML = yearsSorted.map(year => `
      <div class="location-group">
        <div class="location-label">${year}</div>
        <div class="card-grid">${byYear[year].map(m => `
          <div class="card" onclick="loadPhotosByLocation('${esc(country)}','${esc(city)}','${m.year}','${m.month}')">
            <div class="card-title">${m.month}</div>
            <div class="card-count">${m.count.toLocaleString()} photos</div>
          </div>`).join("")}
        </div>
      </div>`).join("");
  } catch (e) {
    el.innerHTML = `<div class="empty">Could not load years for ${city}.</div>`;
  }
}

async function loadPhotosByLocation(country, city, year, month) {
  locationStack = [country, city, year, month];
  const el = document.getElementById("location-content");
  el.innerHTML = `<div class="empty"><span class="spinner"></span>Loading ${city} — ${month} ${year}…</div>`;
  updateLocationHeader();
  try {
    const photos = await api(`/api/locations/${encodeURIComponent(country)}/${encodeURIComponent(city)}/photos?year=${year}&month=${month}`);
    el.innerHTML = renderPhotoGroups(photos);
    lazyLoadThumbs(el);
  } catch (e) {
    el.innerHTML = `<div class="empty">Could not load photos.</div>`;
  }
}

function updateLocationHeader() {
  const el = document.getElementById("location-header");
  if (locationStack.length === 0) {
    el.innerHTML = `<span class="section-title">Browse by Location</span>`;
  } else if (locationStack.length === 2) {
    const [country, city] = locationStack;
    el.innerHTML = `
      <a class="back-link" onclick="loadLocations()">← All locations</a>
      <span class="section-title">${city}</span>`;
  } else {
    const [country, city, year, month] = locationStack;
    el.innerHTML = `
      <a class="back-link" onclick="loadLocations()">← All locations</a>
      <a class="back-link" onclick="loadLocationYears('${esc(country)}','${esc(city)}')">${city}</a>
      <span class="section-title">${month} ${year}</span>`;
  }
}

// ── Shared photo rendering ────────────────────────────────────────────────────

function renderPhotoGroups(photos) {
  if (!photos.length) return `<div class="empty">No photos found.</div>`;

  // Group by location label
  const groups = {};
  photos.forEach(p => {
    const label = p.city
      ? (p.country === "US" ? `${p.state_or_region} / ${p.city}` : `${p.country} / ${p.city}`)
      : (p.folder_description || "Other");
    if (!groups[label]) groups[label] = [];
    groups[label].push(p);
  });

  return Object.entries(groups).map(([label, items]) => `
    <div class="location-group">
      <div class="location-label">${label}</div>
      <div class="photo-grid">${items.map(p => `
        <div class="photo-tile" data-path="${escAttr(p.new_path)}" onclick="openLightbox('${esc(p.new_path)}')">
          <div class="loading">…</div>
        </div>`).join("")}
      </div>
    </div>`).join("");
}

function lazyLoadThumbs(container) {
  const tiles = container.querySelectorAll(".photo-tile[data-path]");
  const obs = new IntersectionObserver((entries) => {
    entries.forEach(entry => {
      if (!entry.isIntersecting) return;
      const tile = entry.target;
      const path = tile.dataset.path;
      obs.unobserve(tile);
      const img = document.createElement("img");
      img.alt = "";
      img.onload = () => {
        const overlay = tile.querySelector(".clump-thumb-overlay");
        tile.innerHTML = "";
        tile.appendChild(img);
        if (overlay) tile.appendChild(overlay);
      };
      img.onerror = () => {
        const loading = tile.querySelector(".loading");
        if (loading) loading.textContent = "✗";
      };
      img.src = thumbUrl(path);
    });
  }, { rootMargin: "200px" });
  tiles.forEach(t => obs.observe(t));
}

// ── Tag view ──────────────────────────────────────────────────────────────────

let clumps = [];
let selectedClump = null;
let scanSelections = {}; // { clumpIndex: Set<path> }

async function loadClumps() {
  const el = document.getElementById("tag-content");
  el.innerHTML = `<div class="empty"><span class="spinner"></span>Loading clumps…</div>`;
  try {
    clumps = await api("/api/clumps");
    renderClumpList();
  } catch (e) {
    el.innerHTML = `<div class="empty">Could not load clumps. Is the VM running?</div>`;
  }
}

function renderClumpList() {
  const el = document.getElementById("tag-content");
  if (!clumps.length) {
    el.innerHTML = `<div class="empty">All clumps tagged! 🎉</div>`;
    return;
  }
  el.innerHTML = `
    <div class="clump-progress">${clumps.length} clump${clumps.length !== 1 ? "s" : ""} remaining</div>
    <div class="clump-list">
      ${clumps.map((c, i) => `
        <div class="clump-row ${selectedClump === i ? "clump-row--active" : ""}"
             onclick="selectClump(${i})">
          <div class="clump-meta">
            <div class="clump-date">${fmtDate(c.start_date)}${c.start_date !== c.end_date ? " – " + fmtDate(c.end_date) : ""}</div>
            <div class="clump-camera">${c.cam_make} ${c.cam_model} · ${c.photo_count} photos</div>
          </div>
          <div class="clump-thumbs">
            ${c.sample_paths.map(p => {
              const sel = scanSelections[i] && scanSelections[i].has(p);
              return `<div class="clump-thumb${sel ? " clump-thumb--selected" : ""}" data-path="${escAttr(p)}" onclick="event.stopPropagation();openLightbox('${esc(p)}')">
                <div class="loading">…</div>
                <div class="clump-thumb-overlay">
                  <button class="thumb-action" onclick="toggleScanSelect(event,this,${i},'${esc(p)}')">🤖 Scan</button>
                  <button class="thumb-action" onclick="event.stopPropagation();openLightbox('${esc(p)}')">↗ Open</button>
                </div>
              </div>`;
            }).join("")}
          </div>
          ${selectedClump === i ? renderClumpDetail(c, i) : ""}
        </div>`).join("")}
    </div>`;

  // touch long-press + lazy-load clump thumbnails
  initTouchOverlays(el);
  document.querySelectorAll(".clump-thumb[data-path]").forEach(tile => {
    const img = document.createElement("img");
    img.alt = "";
    img.onload = () => {
      const overlay = tile.querySelector(".clump-thumb-overlay");
      tile.innerHTML = "";
      tile.appendChild(img);
      if (overlay) tile.appendChild(overlay);
    };
    img.src = thumbUrl(tile.dataset.path);
  });
}

function renderClumpDetail(c, i) {
  const hint = c.ai_hint ? `<div class="clump-hint">🤖 ${esc(c.ai_hint)}</div>` : "";
  return `
    <div class="clump-detail" onclick="event.stopPropagation()">
      ${hint}
      <div class="clump-actions">
        <button class="btn btn--secondary" id="scan-btn-${i}" onclick="scanClump(event, ${i})">🔍 Scan for clues</button>
        <button class="btn btn--secondary" id="photos-btn-${i}" onclick="loadClumpPhotos(event, ${i})">📷 View all ${c.photo_count} photos</button>
        <button class="btn btn--danger" onclick="trashClump(event, ${i})">🗑️ Trash clump</button>
      </div>
      <div class="clump-tag-form">
        <input class="tag-input" id="tag-city-${i}" type="text" placeholder="City" autocomplete="off">
        <input class="tag-input" id="tag-country-${i}" type="text" placeholder="State or Country" autocomplete="off">
        <button class="btn btn--primary" onclick="tagClump(event, ${i})">✓ Tag clump</button>
      </div>
      <div id="scan-result-${i}"></div>
      <div id="clump-photos-${i}"></div>
    </div>`;
}

function toggleScanSelect(e, btn, i, path) {
  e.stopPropagation();
  if (!scanSelections[i]) scanSelections[i] = new Set();
  const sel = scanSelections[i];
  sel.has(path) ? sel.delete(path) : sel.add(path);
  const selected = sel.has(path);
  const tile = btn.closest("[data-path]");
  if (tile) tile.classList.toggle("clump-thumb--selected", selected);
  // Update all Scan buttons for this path to reflect selection state
  document.querySelectorAll(`[data-path="${CSS.escape(path)}"] .thumb-action`).forEach(b => {
    if (b.textContent.includes("Scan") || b.textContent.includes("✓")) {
      b.textContent = selected ? "✓ Selected" : "🤖 Scan";
    }
  });
}

function selectClump(i) {
  const closing = selectedClump === i;
  selectedClump = closing ? null : i;
  renderClumpList();
  if (!closing && !clumps[i].ai_hint) {
    fetchAutoHint(i);
  }
}

async function fetchAutoHint(i) {
  const c = clumps[i];
  const el = document.getElementById(`scan-result-${i}`);
  if (!el) return;
  el.innerHTML = `<div class="clump-hint" style="opacity:0.6">⏳ Checking tags for location clues…</div>`;
  try {
    const data = await api("/api/clumps/hint", {
      method: "POST",
      body: JSON.stringify({ cam_make: c.cam_make, cam_model: c.cam_model,
                             start_date: c.start_date, end_date: c.end_date }),
    });
    clumps[i].ai_hint = data.hint;
    if (el) el.innerHTML = `<div class="clump-hint">🏷️ <em style="font-size:11px;opacity:0.7">(from tags)</em><br>${esc(data.hint)}</div>`;
  } catch (e) {
    if (el) el.innerHTML = "";
  }
}

function fmtDate(iso) {
  if (!iso) return "Unknown";
  return iso.slice(0, 10);
}

async function trashClump(e, i) {
  e.stopPropagation();
  const c = clumps[i];
  if (!confirm(`Trash ${c.photo_count} photos from ${fmtDate(c.start_date)}?`)) return;
  try {
    await api("/api/clumps/trash", {
      method: "POST",
      body: JSON.stringify({ cam_make: c.cam_make, cam_model: c.cam_model,
                             start_date: c.start_date, end_date: c.end_date }),
    });
    clumps.splice(i, 1);
    selectedClump = null;
    renderClumpList();
  } catch (err) {
    alert("Trash failed: " + err.message);
  }
}

async function scanClump(e, i) {
  e.stopPropagation();
  const c = clumps[i];
  const btn = document.getElementById(`scan-btn-${i}`);
  const result = document.getElementById(`scan-result-${i}`);
  btn.disabled = true;
  const selected = scanSelections[i] ? [...scanSelections[i]] : [];
  btn.textContent = selected.length
    ? `⏳ Scanning ${selected.length} selected…`
    : "⏳ Scanning…";
  try {
    const data = await api("/api/clumps/scan", {
      method: "POST",
      body: JSON.stringify({
        cam_make: c.cam_make, cam_model: c.cam_model,
        start_date: c.start_date, end_date: c.end_date,
        ...(selected.length ? { paths: selected } : {}),
      }),
    });
    clumps[i].ai_hint = data.hint;
    const scope = selected.length ? `${selected.length} selected photo${selected.length > 1 ? "s" : ""}` : `all ${c.photo_count} photos`;
    result.innerHTML = `<div class="clump-hint">🤖 <em style="font-size:11px;opacity:0.7">(scanned ${scope})</em><br>${esc(data.description)}</div>`;
    btn.disabled = false;
    btn.textContent = "🔍 Scan again";
  } catch (err) {
    btn.disabled = false;
    btn.textContent = "🔍 Scan for clues";
    result.innerHTML = `<div class="clump-hint" style="color:red">Scan failed: ${esc(err.message)}</div>`;
  }
}

async function loadClumpPhotos(e, i) {
  e.stopPropagation();
  const c = clumps[i];
  const btn = document.getElementById(`photos-btn-${i}`);
  const container = document.getElementById(`clump-photos-${i}`);

  if (container.dataset.loaded) {
    const hidden = container.style.display === "none";
    container.style.display = hidden ? "" : "none";
    btn.textContent = hidden ? "📷 Hide photos" : `📷 View all ${c.photo_count} photos`;
    return;
  }

  btn.disabled = true;
  btn.textContent = "⏳ Loading…";
  try {
    const photos = await api(
      `/api/clumps/photos?cam_make=${encodeURIComponent(c.cam_make)}&cam_model=${encodeURIComponent(c.cam_model)}&start_date=${encodeURIComponent(c.start_date)}&end_date=${encodeURIComponent(c.end_date)}`
    );
    container.innerHTML = `<div class="photo-grid" style="margin-top:12px">${photos.map(p => `
      <div class="photo-tile" data-path="${escAttr(p.new_path)}" onclick="event.stopPropagation();openLightbox('${esc(p.new_path)}')">
        <div class="loading">…</div>
        <div class="clump-thumb-overlay">
          <button class="thumb-action" onclick="toggleScanSelect(event,this,${i},'${esc(p.new_path)}')">🤖 Scan</button>
          <button class="thumb-action" onclick="event.stopPropagation();openLightbox('${esc(p.new_path)}')">↗ Open</button>
        </div>
      </div>`).join("")}</div>`;
    container.dataset.loaded = "1";
    initTouchOverlays(container);
    lazyLoadThumbs(container);
    btn.disabled = false;
    btn.textContent = "📷 Hide photos";
    container.scrollIntoView({ behavior: "smooth", block: "nearest" });
  } catch (err) {
    btn.disabled = false;
    btn.textContent = `📷 View all ${c.photo_count} photos`;
    container.innerHTML = `<div style="color:red;font-size:13px">Could not load photos: ${esc(err.message)}</div>`;
  }
}

async function tagClump(e, i) {
  e.stopPropagation();
  const c = clumps[i];
  const city = document.getElementById(`tag-city-${i}`).value.trim();
  const country = document.getElementById(`tag-country-${i}`).value.trim();
  if (!city || !country) { alert("Please enter both city and country."); return; }
  try {
    await api("/api/clumps/tag", {
      method: "POST",
      body: JSON.stringify({ cam_make: c.cam_make, cam_model: c.cam_model,
                             start_date: c.start_date, end_date: c.end_date,
                             tagged_city: city, tagged_country: country }),
    });
    clumps.splice(i, 1);
    selectedClump = null;
    renderClumpList();
  } catch (err) {
    alert("Tag failed: " + err.message);
  }
}

// ── Search view ───────────────────────────────────────────────────────────────

async function doSearch() {
  const q = document.getElementById("search-input").value.trim();
  if (!q) return;
  searchState = { q, offset: 0, total: 0 };
  const el = document.getElementById("search-content");
  el.innerHTML = `<div class="empty"><span class="spinner"></span>Searching…</div>`;
  try {
    const data = await api(`/api/search?q=${encodeURIComponent(q)}&limit=50&offset=0`);
    searchState.total = data.total;
    if (!data.photos.length) {
      el.innerHTML = `<div class="empty">No photos found for "${esc(q)}".</div>`;
      return;
    }
    el.innerHTML = renderSearchGroups(data.photos, data.total);
    lazyLoadThumbs(el);
    if (data.total > 50) {
      el.insertAdjacentHTML("beforeend",
        `<div class="search-more">
           <span class="search-count">${data.total.toLocaleString()} total — showing first 50</span>
           <button class="btn btn--secondary" onclick="loadMoreSearch()">Load more</button>
         </div>`);
    } else {
      el.insertAdjacentHTML("beforeend",
        `<div class="search-count">${data.total.toLocaleString()} photo${data.total !== 1 ? "s" : ""} found</div>`);
    }
  } catch (e) {
    el.innerHTML = `<div class="empty">Search failed. Is the VM running?</div>`;
  }
}

async function loadMoreSearch() {
  const { q, total } = searchState;
  searchState.offset += 50;
  const el = document.getElementById("search-content");
  const moreEl = el.querySelector(".search-more");
  if (moreEl) moreEl.innerHTML = `<span class="spinner"></span>`;
  try {
    const data = await api(`/api/search?q=${encodeURIComponent(q)}&limit=50&offset=${searchState.offset}`);
    if (moreEl) moreEl.remove();
    const newHtml = renderSearchGroups(data.photos, 0);
    el.insertAdjacentHTML("beforeend", newHtml);
    lazyLoadThumbs(el);
    const loaded = searchState.offset + data.photos.length;
    if (loaded < total) {
      el.insertAdjacentHTML("beforeend",
        `<div class="search-more">
           <span class="search-count">${loaded.toLocaleString()} of ${total.toLocaleString()}</span>
           <button class="btn btn--secondary" onclick="loadMoreSearch()">Load more</button>
         </div>`);
    } else {
      el.insertAdjacentHTML("beforeend",
        `<div class="search-count">All ${total.toLocaleString()} photos loaded</div>`);
    }
  } catch (e) {
    if (moreEl) moreEl.innerHTML = `<span style="color:red">Failed to load more.</span>`;
  }
}

function renderSearchGroups(photos, total) {
  if (!photos.length) return "";
  const groups = {};
  photos.forEach(p => {
    const label = p.year || "Unknown";
    if (!groups[label]) groups[label] = [];
    groups[label].push(p);
  });
  return Object.keys(groups).map(year => `
    <div class="location-group">
      <div class="location-label">${year}</div>
      <div class="photo-grid">${groups[year].map(p => `
        <div class="photo-tile" data-path="${escAttr(p.new_path)}" onclick="openLightbox('${esc(p.new_path)}')">
          <div class="loading">…</div>
        </div>`).join("")}
      </div>
    </div>`).join("");
}

// ── Touch long-press overlay (iPhone) ────────────────────────────────────────

function initTouchOverlays(container) {
  container.querySelectorAll(".clump-thumb[data-path], .photo-tile[data-path]").forEach(tile => {
    let timer = null;
    const path = tile.dataset.path;

    tile.addEventListener("touchstart", () => {
      timer = setTimeout(() => {
        timer = null;
        // Dismiss any other open overlays first
        document.querySelectorAll(".thumb-overlay-active").forEach(el => el.classList.remove("thumb-overlay-active"));
        tile.classList.add("thumb-overlay-active");
      }, 500);
    });

    tile.addEventListener("touchmove", () => { clearTimeout(timer); timer = null; });

    tile.addEventListener("touchend", (e) => {
      if (timer !== null) {
        clearTimeout(timer); timer = null;
        openLightbox(path);
      }
    });

    tile.addEventListener("touchcancel", () => { clearTimeout(timer); timer = null; });
    tile.addEventListener("contextmenu", (e) => e.preventDefault());
  });
}

// Dismiss overlay when tapping elsewhere
document.addEventListener("touchstart", (e) => {
  if (!e.target.closest(".thumb-overlay-active")) {
    document.querySelectorAll(".thumb-overlay-active").forEach(el => el.classList.remove("thumb-overlay-active"));
  }
}, { passive: true });

// ── Lightbox ──────────────────────────────────────────────────────────────────

(function() {
  const overlay = document.getElementById("lightbox");

  function close() { overlay.classList.remove("open"); }
  overlay.querySelector("#lightbox-backdrop").addEventListener("click", close);
  overlay.querySelector("#lightbox-close").addEventListener("click", close);
  document.addEventListener("keydown", e => { if (e.key === "Escape") close(); });

  window.openLightbox = function(path) {
    const img = overlay.querySelector("#lightbox-img");
    const spinner = overlay.querySelector("#lightbox-spinner");
    img.style.display = "none";
    spinner.style.display = "block";
    overlay.classList.add("open");
    img.onload = () => { spinner.style.display = "none"; img.style.display = "block"; };
    img.onerror = () => { spinner.style.display = "none"; img.alt = "Could not load photo."; img.style.display = "block"; };
    img.src = API_BASE + "/api/photo?path=" + encodeURIComponent(path);
  };
})();

// ── Utils ─────────────────────────────────────────────────────────────────────

function esc(s) {
  return String(s || "").replace(/'/g, "\\'").replace(/"/g, "&quot;");
}

function escAttr(s) {
  return String(s || "").replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ── Boot ──────────────────────────────────────────────────────────────────────

loadStats();
showView("home");
