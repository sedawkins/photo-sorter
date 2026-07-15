/* Photo Sorter — SPA frontend (Phase 1: Date + Location browse views) */

const API_KEY = window.PHOTO_API_KEY || "";
const API_BASE = window.API_BASE || "";  // e.g. "https://photo-sorter-vm.westus2.cloudapp.azure.com:8000"

// ── API helpers ───────────────────────────────────────────────────────────────

async function api(path) {
  const headers = API_KEY ? { "X-API-Key": API_KEY } : {};
  const resp = await fetch(API_BASE + path, { headers });
  if (!resp.ok) throw new Error(`API error ${resp.status} for ${path}`);
  return resp.json();
}

function thumbUrl(photoPath) {
  return API_BASE + "/api/thumb?path=" + encodeURIComponent(photoPath);
}

// ── State ─────────────────────────────────────────────────────────────────────

let currentView = "date";    // "date" | "location"
let dateStack = [];          // breadcrumb: [] | [year] | [year, month]
let locationStack = [];      // [] | [country, city] | [country, city, year, month]

// ── Nav ───────────────────────────────────────────────────────────────────────

document.getElementById("btn-date").addEventListener("click", () => showView("date"));
document.getElementById("btn-location").addEventListener("click", () => showView("location"));

function showView(view) {
  currentView = view;
  document.querySelectorAll(".view").forEach(el => el.classList.remove("active"));
  document.querySelectorAll(".nav-btn").forEach(el => el.classList.remove("active"));
  document.getElementById("view-" + view).classList.add("active");
  document.getElementById("btn-" + view).classList.add("active");

  if (view === "date" && dateStack.length === 0) loadYears();
  if (view === "location" && locationStack.length === 0) loadLocations();
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

async function loadLocations() {
  locationStack = [];
  const el = document.getElementById("location-content");
  el.innerHTML = `<div class="empty"><span class="spinner"></span>Loading locations…</div>`;
  updateLocationHeader();
  try {
    const locs = await api("/api/locations");
    if (!locs.length) { el.innerHTML = `<div class="empty">No geotagged photos found.</div>`; return; }
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
    el.innerHTML = `<div class="empty">Could not load locations. Is the VM running?</div>`;
  }
}

async function loadLocationYears(country, city) {
  locationStack = [country, city];
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
        <div class="photo-tile" data-path="${esc(p.new_path)}">
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
      img.onload = () => { tile.innerHTML = ""; tile.appendChild(img); };
      img.onerror = () => { tile.querySelector(".loading").textContent = "✗"; };
      img.src = thumbUrl(path);
    });
  }, { rootMargin: "200px" });
  tiles.forEach(t => obs.observe(t));
}

// ── Utils ─────────────────────────────────────────────────────────────────────

function esc(s) {
  return String(s || "").replace(/'/g, "\\'").replace(/"/g, "&quot;");
}

// ── Boot ──────────────────────────────────────────────────────────────────────

loadStats();
showView("date");
