const $ = (id) => document.getElementById(id);
const fmt = (n) => (n ?? 0).toLocaleString("en-US");
const api = (p) => fetch(p).then((r) => r.json());

// Lazy thumbnails: only fetch a preview when the tile scrolls into view.
const thumbObserver = new IntersectionObserver(
  (entries, obs) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      const img = e.target;
      obs.unobserve(img);
      img.src = "/api/thumb?path=" + encodeURIComponent(img.dataset.path);
      img.addEventListener("load", () => img.classList.add("in"), { once: true });
      img.addEventListener("error", () => (img.style.background = "var(--tile)"), { once: true });
    }
  },
  { rootMargin: "300px" }
);

function eagerThumb(el, path, size) {
  const img = new Image();
  img.onload = () => {
    el.querySelector("img")?.remove();
    el.prepend(img);
  };
  img.src = "/api/thumb?path=" + encodeURIComponent(path) + (size ? "&size=" + size : "");
}

// ---------------------------------------------------------------- hero
async function loadHero(s) {
  $("hero-lede").textContent =
    `${fmt(s.photos)} photos and ${fmt(s.movies)} movies, spanning ${s.year_span}, ` +
    `across ${fmt(s.cities)} places — one browsable order at last.`;
  const figs = [
    [fmt(s.photos), "photos kept"],
    [fmt(s.cleared), "duplicates cleared"],
    [fmt(s.cities), "places"],
    [s.year_span, "years"],
  ];
  $("hero-figures").innerHTML = figs
    .map((f) => `<div class="fig"><div class="n mono">${f[0]}</div><div class="l">${f[1]}</div></div>`)
    .join("");
}

// ---------------------------------------------------------------- years
async function loadYears() {
  const { years } = await api("/api/timeline");
  const max = Math.max(...years.map((y) => y.n), 1);
  const ribbon = $("ribbon");
  ribbon.innerHTML = years
    .map(
      (y) =>
        `<div class="bar" style="height:${Math.max(3, (y.n / max) * 100)}%" data-year="${y.year}">` +
        `<span class="tip mono">${y.year} · ${fmt(y.n)}</span></div>`
    )
    .join("");
  ribbon.querySelectorAll(".bar").forEach((b) =>
    b.addEventListener("click", () => openGrid({ year: b.dataset.year }))
  );
  if (years.length) {
    $("axis").innerHTML =
      `<span>${years[0].year}</span><span>${years[years.length - 1].year}</span>`;
  }
}

// ---------------------------------------------------------------- places
async function loadPlaces() {
  const { places } = await api("/api/places");
  const list = $("placelist");
  list.innerHTML = places
    .slice(0, 12)
    .map(
      (p) =>
        `<div class="row" data-city="${encodeURIComponent(p.city)}" data-state="${encodeURIComponent(p.state_or_region || "")}">` +
        `<span class="city">${p.label}</span>` +
        `<span class="region">${p.region || ""}</span>` +
        `<span class="cnt mono">${fmt(p.n)}</span></div>`
    )
    .join("");
  list.querySelectorAll(".row").forEach((r) =>
    r.addEventListener("click", () =>
      openGrid({
        city: decodeURIComponent(r.dataset.city),
        state: decodeURIComponent(r.dataset.state) || undefined,
      })
    )
  );
  drawMap(places);
}

function loadLeaflet() {
  if (window._leafletPromise) return window._leafletPromise;
  window._leafletPromise = new Promise((resolve, reject) => {
    if (typeof L !== "undefined") return resolve();
    const css = document.createElement("link");
    css.rel = "stylesheet";
    css.href = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.css";
    document.head.appendChild(css);
    const js = document.createElement("script");
    js.src = "https://unpkg.com/leaflet@1.9.4/dist/leaflet.js";
    js.onload = () => resolve();
    js.onerror = () => reject(new Error("leaflet blocked"));
    document.body.appendChild(js);
  });
  return window._leafletPromise;
}

async function drawMap(places) {
  try {
    await loadLeaflet();
  } catch (e) {
    $("map").style.display = "none"; // no network for tiles — the ranked list stands alone
    document.querySelector(".places").style.gridTemplateColumns = "1fr";
    return;
  }
  const dark = matchMedia("(prefers-color-scheme: dark)").matches;
  const map = L.map("map", { scrollWheelZoom: false, attributionControl: false }).setView([25, 5], 1);
  const tiles = dark
    ? "https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png"
    : "https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png";
  L.tileLayer(tiles, { maxZoom: 12 }).addTo(map);
  const max = Math.max(...places.map((p) => p.n), 1);
  const ink = dark ? "#ededed" : "#000";
  places.forEach((p) => {
    if (p.lat == null || p.lon == null) return;
    const r = 3 + Math.sqrt(p.n / max) * 16;
    L.circleMarker([p.lat, p.lon], {
      radius: r, color: ink, weight: 1, fillColor: ink, fillOpacity: 0.35,
    })
      .bindTooltip(`${p.label} · ${fmt(p.n)}`, { direction: "top" })
      .on("click", () =>
        openGrid({ city: p.city, state: p.country === "US" ? p.state_or_region : undefined })
      )
      .addTo(map);
  });
}

// ---------------------------------------------------------------- story
async function loadStory(s) {
  $("flow").innerHTML =
    `<div class="step"><div class="n mono">${fmt(s.seen)}</div><div class="l">files seen across every folder and drive</div></div>` +
    `<div class="arrow">→</div>` +
    `<div class="step"><div class="n mono">${fmt(s.cleared)}</div><div class="l">duplicates cleared, never copied twice</div></div>` +
    `<div class="arrow">→</div>` +
    `<div class="step"><div class="n mono">${fmt(s.kept)}</div><div class="l">kept and sorted by time and place</div></div>`;
  const keptPct = (s.kept / s.seen) * 100;
  $("propbar").innerHTML =
    `<div class="kept" style="width:${keptPct}%"></div>` +
    `<div class="cleared" style="width:${100 - keptPct}%"></div>`;
  $("proplabels").innerHTML =
    `<span>${Math.round(keptPct)}% kept</span><span>${Math.round(100 - keptPct)}% were duplicates</span>`;
  $("aside").innerHTML =
    `<b>${fmt(s.needs_where)} photos remember <i>when</i> but not <i>where.</i></b> ` +
    `They have a date but no location yet — a mystery for another day, not a to-do.`;
}

// ---------------------------------------------------------------- memory
async function loadMemory() {
  $("mem-cap").textContent = "…";
  $("mem-frame").innerHTML = "";
  const m = await api("/api/serendipity");
  if (m.error) return;
  eagerThumb($("mem-frame"), m.path, "large");
  $("mem-cap").textContent = m.caption;
  $("mem-sub").textContent = m.filename;
  // seed the hero with the same style of resurfaced photo
}

async function loadHeroPhoto() {
  const m = await api("/api/serendipity");
  if (m.error) return;
  eagerThumb($("hero-photo"), m.path, "large");
  $("hero-cap").textContent = m.caption;
}

// ---------------------------------------------------------------- grid
let gridState = null;
async function openGrid(filter) {
  gridState = { ...filter, offset: 0, total: 0 };
  $("grid").innerHTML = "";
  $("grid-view").classList.add("on");
  $("grid-view").scrollIntoView({ behavior: "smooth", block: "start" });
  await loadGridPage();
}

async function loadGridPage() {
  const q = new URLSearchParams();
  for (const k of ["year", "state", "city"]) if (gridState[k]) q.set(k, gridState[k]);
  q.set("offset", gridState.offset);
  q.set("limit", 120);
  const res = await api("/api/browse?" + q.toString());
  gridState.total = res.total;
  $("grid-title").textContent = res.title;
  $("grid-count").textContent = fmt(res.total) + " photos";
  const grid = $("grid");
  for (const it of res.items) {
    const cell = document.createElement("div");
    cell.className = "cell";
    const img = document.createElement("img");
    img.dataset.path = it.path;
    img.alt = it.caption;
    if (it.media_type === "movie") {
      const b = document.createElement("span");
      b.className = "badge";
      b.textContent = "movie";
      cell.appendChild(b);
    }
    cell.appendChild(img);
    grid.appendChild(cell);
    thumbObserver.observe(img);
  }
  gridState.offset += res.items.length;
  $("grid-more").style.display = gridState.offset < gridState.total ? "inline-block" : "none";
}

$("grid-back").addEventListener("click", () => {
  $("grid-view").classList.remove("on");
  $("top").scrollIntoView({ behavior: "smooth" });
});
$("grid-more").addEventListener("click", loadGridPage);
$("mem-again").addEventListener("click", loadMemory);

// ---------------------------------------------------------------- boot
(async function () {
  const s = await api("/api/summary");
  loadHero(s);
  loadStory(s);
  loadHeroPhoto();
  loadYears();
  loadPlaces();
  loadMemory();
})();
