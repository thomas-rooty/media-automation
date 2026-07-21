"use strict";

const $ = (id) => document.getElementById(id);
const state = {
  refreshSeconds: 30,
  refreshTimer: null,
  countdownTimer: null,
  nextRefreshAt: 0,
  refreshing: false,
  lastStatus: null,
  weatherForecast: null,
  weatherForecastAt: 0,
  systemMode: "live",
  mediaType: "tv",
  currentTvId: null,
  selectedSeasons: new Set(),
};

const REFRESH_OPTIONS = [
  { seconds: 10, label: "10 secondes" },
  { seconds: 30, label: "30 secondes" },
  { seconds: 60, label: "1 minute" },
  { seconds: 180, label: "3 minutes" },
  { seconds: 300, label: "5 minutes" },
  { seconds: 600, label: "10 minutes" },
];

function text(value) {
  return value == null ? "" : String(value).trim();
}

function escapeHtml(value) {
  return text(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function list(value) {
  return Array.isArray(value) ? value : [];
}

function clamp(value, min, max) {
  return Math.max(min, Math.min(max, Number(value) || 0));
}

function formatBytes(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "—";
  const units = ["o", "Ko", "Mo", "Go", "To"];
  let current = Math.max(0, number);
  let unit = 0;
  while (current >= 1024 && unit < units.length - 1) {
    current /= 1024;
    unit += 1;
  }
  const digits = current >= 10 || unit === 0 ? 0 : 1;
  return `${current.toFixed(digits)} ${units[unit]}`;
}

function formatSpeed(value) {
  const number = Number(value);
  return Number.isFinite(number) ? `${formatBytes(number)}/s` : "—";
}

function formatEta(seconds) {
  const value = Number(seconds);
  if (!Number.isFinite(value) || value < 0 || value >= 8_640_000) return "—";
  if (value < 60) return `${Math.round(value)} s`;
  const hours = Math.floor(value / 3600);
  const minutes = Math.floor((value % 3600) / 60);
  return hours ? `${hours} h ${String(minutes).padStart(2, "0")}` : `${minutes} min`;
}

function formatDate(value, options = {}) {
  const date = new Date(value);
  if (!value || Number.isNaN(date.getTime())) return "—";
  return date.toLocaleDateString("fr-FR", options);
}

function formatTime(value) {
  const date = new Date(value);
  if (!value || Number.isNaN(date.getTime())) return "—";
  return date.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
}

function relativeTime(value) {
  const date = new Date(value);
  const seconds = Math.floor((Date.now() - date.getTime()) / 1000);
  if (!value || !Number.isFinite(seconds) || seconds < 0) return "récemment";
  if (seconds < 60) return "à l’instant";
  if (seconds < 3600) return `il y a ${Math.floor(seconds / 60)} min`;
  if (seconds < 172800) return `il y a ${Math.floor(seconds / 3600)} h`;
  return `il y a ${Math.floor(seconds / 86400)} j`;
}

function icon(symbol) {
  return `<svg aria-hidden="true"><use href="#${symbol}"></use></svg>`;
}

async function api(path, options = {}) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 12_000);
  try {
    const response = await fetch(path, {
      cache: "no-store",
      headers: options.body ? { "Content-Type": "application/json" } : undefined,
      ...options,
      signal: controller.signal,
    });
    if (!response.ok) {
      let detail = "";
      try {
        const body = await response.json();
        if (typeof body?.detail === "string") {
          detail = body.detail;
        } else if (body?.detail && typeof body.detail === "object") {
          const message = text(body.detail.message);
          const service = text(body.detail.service);
          const upstreamStatus = Number(body.detail.status);
          detail = [message || service, Number.isFinite(upstreamStatus) ? `HTTP ${upstreamStatus}` : ""]
            .filter(Boolean)
            .join(" · ");
        }
      } catch {}
      const error = new Error(detail || `HTTP ${response.status}`);
      error.status = response.status;
      throw error;
    }
    return await response.json();
  } catch (error) {
    if (error?.name === "AbortError") throw new Error("Délai de réponse dépassé");
    throw error;
  } finally {
    clearTimeout(timeout);
  }
}

function post(path, body) {
  return api(path, { method: "POST", body: JSON.stringify(body) });
}

function openDialog(id) {
  const dialog = $(id);
  if (!dialog) return;
  if (typeof dialog.showModal === "function" && !dialog.open) dialog.showModal();
}

function closeDialog(id) {
  const dialog = $(id);
  if (dialog?.open) dialog.close();
}

function toast(message, kind = "success") {
  const root = $("toast");
  if (!root) return;
  root.textContent = message;
  root.className = `toast show${kind === "error" ? " error" : ""}`;
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => { root.className = "toast"; }, 2800);
}

function renderError(rootId, label, error) {
  const root = $(rootId);
  if (!root) return;
  const message = text(error?.message) || "Service indisponible";
  root.innerHTML = `<div class="error-state"><div><strong>${escapeHtml(label)} indisponible</strong><span>${escapeHtml(message)}</span><button type="button" data-retry>Réessayer</button></div></div>`;
}

function renderEmpty(rootId, title, subtitle = "Rien à afficher pour le moment") {
  const root = $(rootId);
  if (!root) return;
  root.innerHTML = `<div class="empty-state"><div><strong>${escapeHtml(title)}</strong><br><span>${escapeHtml(subtitle)}</span></div></div>`;
}

function tickClock() {
  const now = new Date();
  $("clockTime").textContent = now.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" });
  $("clockDate").textContent = now.toLocaleDateString("fr-FR", { weekday: "short", day: "2-digit", month: "short" });
}

function updateRefreshLabel() {
  const remaining = state.nextRefreshAt
    ? Math.max(0, Math.ceil((state.nextRefreshAt - Date.now()) / 1000))
    : state.refreshSeconds;
  $("refreshLabel").textContent = `${remaining} s`;
}

function scheduleRefresh() {
  clearTimeout(state.refreshTimer);
  state.nextRefreshAt = Date.now() + state.refreshSeconds * 1000;
  updateRefreshLabel();
  state.refreshTimer = setTimeout(async () => {
    await refreshAll();
    scheduleRefresh();
  }, state.refreshSeconds * 1000);
  if (!state.countdownTimer) state.countdownTimer = setInterval(updateRefreshLabel, 1000);
}

function setRefreshSeconds(seconds) {
  const value = Number(seconds);
  if (!REFRESH_OPTIONS.some((option) => option.seconds === value)) return;
  state.refreshSeconds = value;
  try { localStorage.setItem("capyflixRefreshSeconds", String(value)); } catch {}
  renderRefreshChoices();
  scheduleRefresh();
}

function renderRefreshChoices() {
  const root = $("refreshChoices");
  if (!root) return;
  root.innerHTML = REFRESH_OPTIONS.map((option) => `
    <button class="choice-button${option.seconds === state.refreshSeconds ? " active" : ""}" type="button" data-refresh-seconds="${option.seconds}">
      ${escapeHtml(option.label)}
    </button>
  `).join("");
}

function categoryLabel(category) {
  return ({
    network: "Réseau",
    downloads: "Téléchargements",
    indexers: "Indexeurs",
    library: "Bibliothèque",
    media: "Média",
    tools: "Outils",
  })[category] || "Service";
}

function renderStatus(data) {
  state.lastStatus = data;
  const items = list(data?.items);
  const summary = data?.summary || {};
  const healthy = Number(summary.healthy ?? items.filter((item) => item.ok).length);
  const total = Number(summary.total ?? items.length);
  const failing = Math.max(0, total - healthy);
  const allGood = total > 0 && failing === 0;

  const dot = $("statusDot");
  dot.className = `status-dot ${allGood ? "good" : "bad"}`;
  $("statusText").textContent = allGood ? "Tous les services répondent" : `${failing} service${failing > 1 ? "s" : ""} à vérifier`;
  $("statusDetail").textContent = `${healthy}/${total} opérationnels${data?.dockerAvailable === false ? " · Docker non visible" : ""}`;
  $("overviewHeadline").textContent = allGood ? "Opérationnel" : `${healthy} services sur ${total} opérationnels`;

  const strip = $("serviceStrip");
  strip.innerHTML = items.map((item) => `
    <div class="service-chip${item.ok ? "" : " down"}" title="${escapeHtml(item.detail)}">
      <span class="mini-dot"></span><span>${escapeHtml(item.name)}</span>
    </div>
  `).join("") || `<div class="service-chip down"><span class="mini-dot"></span>Aucun service détecté</div>`;

  renderServicesModal(data);
}

function renderServicesModal(data) {
  const root = $("servicesList");
  const summaryRoot = $("servicesSummary");
  if (!root || !summaryRoot) return;
  const items = list(data?.items).slice().sort((a, b) => Number(a.ok) - Number(b.ok));
  const summary = data?.summary || {};
  summaryRoot.textContent = `${summary.healthy ?? 0} opérationnels · ${summary.down ?? 0} hors ligne · ${summary.unconfigured ?? 0} à configurer · ${summary.unknown ?? 0} non vérifiés${data?.dockerAvailable === false ? " · accès Docker indisponible" : ""}`;
  root.innerHTML = items.map((item) => `
    <div class="service-row">
      <div class="service-main">
        <span class="service-state${item.ok ? "" : " down"}"></span>
        <div><div class="service-name">${escapeHtml(item.name)}</div><div class="service-category">${escapeHtml(categoryLabel(item.category))} · ${escapeHtml(item.state)}</div></div>
      </div>
      <div class="service-detail"><strong>${escapeHtml(item.ok ? "Opérationnel" : "À vérifier")}</strong>${escapeHtml(item.detail)}${item.responseMs != null && Number.isFinite(Number(item.responseMs)) ? ` · ${Number(item.responseMs)} ms` : ""}</div>
    </div>
  `).join("") || `<div class="empty-state">Aucune donnée de supervision.</div>`;
}

function qbState(stateName) {
  const value = text(stateName).toLowerCase();
  if (value.includes("stalled")) return { label: "Bloqué", className: "bad" };
  if (value.includes("queued")) return { label: "En file", className: "warn" };
  if (value.includes("check")) return { label: "Vérification", className: "warn" };
  if (/downloading|forceddl|metadl|allocating/.test(value)) return { label: "Télécharge", className: "good" };
  if (/uploading|seeding/.test(value)) return { label: "Partage", className: "good" };
  return { label: value || "Inconnu", className: "warn" };
}

function renderQb(data) {
  const items = list(data?.items);
  $("qbCount").textContent = String(items.length);
  if (!items.length) {
    renderEmpty("qbList", "File au repos", "Aucun téléchargement actif");
    return;
  }
  $("qbList").innerHTML = items.map((item) => {
    const progress = clamp(item.progress, 0, 1);
    const stateInfo = qbState(item.state);
    return `
      <div class="list-row">
        <div class="row-copy">
          <div class="row-title">${escapeHtml(item.name || "Torrent")}</div>
          <div class="download-stats">
            <span>↓ ${escapeHtml(formatSpeed(item.dlspeed))}</span><span>↑ ${escapeHtml(formatSpeed(item.upspeed))}</span><span>Reste ${escapeHtml(formatBytes(item.amount_left))}</span><span>ETA ${escapeHtml(formatEta(item.eta))}</span>
          </div>
          <div class="progress-track"><span style="width:${(progress * 100).toFixed(1)}%"></span></div>
        </div>
        <div class="row-meta"><span class="badge ${stateInfo.className}">${escapeHtml(stateInfo.label)}</span><strong>${Math.round(progress * 100)} %</strong></div>
      </div>
    `;
  }).join("");
}

function mediaBadge(item) {
  const played = item?.userData?.played === true;
  const position = Number(item?.userData?.playbackPositionTicks || 0);
  if (played) return `<span class="badge good media-badge">Vu</span>`;
  if (position > 0) return `<span class="badge warn media-badge">En cours</span>`;
  return `<span class="badge media-badge">Nouveau</span>`;
}

function renderJelly(data) {
  const items = list(data?.items);
  if (!items.length) {
    renderEmpty("jellyList", "Bibliothèque à jour", "Jellyfin n’a renvoyé aucun ajout récent");
    return;
  }
  $("jellyList").innerHTML = items.slice(0, 8).map((item) => {
    const episode = text(item.type).toLowerCase() === "episode";
    const episodeCode = episode ? `S${String(item.parentIndexNumber ?? "?").padStart(2, "0")}E${String(item.indexNumber ?? "?").padStart(2, "0")}` : "";
    const title = episode ? `${item.seriesName || "Série"} · ${episodeCode}` : (item.name || "Média");
    const subtitle = episode ? item.name : [item.type, item.productionYear].filter(Boolean).join(" · ");
    const imageUrl = item.id ? `/api/jellyfin/items/${encodeURIComponent(item.id)}/image?maxHeight=260&quality=82` : "";
    return `
      <article class="media-item">
        <div class="poster">
          ${imageUrl ? `<img src="${imageUrl}" alt="" loading="lazy">` : `<div class="poster-placeholder">${icon("i-movie")}</div>`}
          ${mediaBadge(item)}
        </div>
        <div class="media-name" title="${escapeHtml(title)}">${escapeHtml(title)}</div>
        <div class="media-sub">${escapeHtml(subtitle)} · ${escapeHtml(relativeTime(item.dateCreated))}</div>
      </article>
    `;
  }).join("");
  $("jellyList").querySelectorAll("img").forEach((image) => {
    image.addEventListener("error", () => {
      image.replaceWith(Object.assign(document.createElement("div"), { className: "poster-placeholder", innerHTML: icon("i-movie") }));
    }, { once: true });
  });
}

function startOfWeek(date) {
  const day = date.getDay();
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() - ((day + 6) % 7));
}

function sonarrBucket(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return { key: "unknown", label: "Date inconnue" };
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const target = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const diff = Math.round((target - today) / 86400000);
  if (diff === 0) return { key: "today", label: "Aujourd’hui" };
  if (diff === 1) return { key: "tomorrow", label: "Demain" };
  if (diff < 0) return { key: "past", label: "Passé" };
  const week = Math.round((startOfWeek(target) - startOfWeek(today)) / 86400000);
  if (week === 0) return { key: "week", label: "Cette semaine" };
  if (week === 7) return { key: "next", label: "Semaine prochaine" };
  return { key: "later", label: "Plus tard" };
}

function renderSonarr(data) {
  const items = list(data?.items).slice().sort((a, b) => text(a.airDateUtc).localeCompare(text(b.airDateUtc)));
  if (!items.length) {
    renderEmpty("sonarrList", "Rien de prévu", "Aucun épisode dans les 14 prochains jours");
    return;
  }
  let lastBucket = "";
  const html = [];
  items.forEach((item) => {
    const bucket = sonarrBucket(item.airDateUtc);
    if (bucket.key !== lastBucket) {
      lastBucket = bucket.key;
      html.push(`<div class="section-label">${escapeHtml(bucket.label)}</div>`);
    }
    const episode = `S${String(item.seasonNumber ?? "?").padStart(2, "0")}E${String(item.episodeNumber ?? "?").padStart(2, "0")}`;
    html.push(`
      <div class="list-row">
        <div class="row-copy"><div class="row-title">${escapeHtml(item.seriesTitle || "Série")}</div><div class="row-subtitle">${escapeHtml(episode)} · ${escapeHtml(item.episodeTitle || "Épisode")}</div></div>
        <div class="row-meta"><strong>${escapeHtml(formatDate(item.airDateUtc, { weekday: "short", day: "2-digit", month: "short" }))}</strong>${escapeHtml(formatTime(item.airDateUtc))}</div>
      </div>
    `);
  });
  $("sonarrList").innerHTML = html.join("");
}

function renderImporting(data) {
  const root = $("sonarrImporting");
  const items = list(data?.items);
  if (!items.length) {
    root.classList.add("hidden");
    root.textContent = "";
    return;
  }
  root.classList.remove("hidden");
  root.textContent = `${items.length} épisode${items.length > 1 ? "s" : ""} en cours d’importation`;
}

function dayBucket(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return { key: "unknown", label: "Date inconnue" };
  const now = new Date();
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const target = new Date(date.getFullYear(), date.getMonth(), date.getDate());
  const diff = Math.round((target - today) / 86400000);
  if (diff < 0) return { key: "missing", label: "Déjà sortis · manquants" };
  if (diff <= 7) return { key: "soon", label: "Dans les 7 jours" };
  if (diff <= 31) return { key: "month", label: "Ce mois-ci" };
  return { key: "later", label: "Plus tard" };
}

function reasonBadge(item) {
  if (item.reason === "Queued") return `<span class="badge good">En file</span>`;
  if (item.reason === "Missing") return `<button class="badge warn" type="button" data-radarr-legend>Manquant ${list(item.missingIcons).map(escapeHtml).join("")}</button>`;
  return `<span class="badge">À venir</span>`;
}

function renderRadarr(data) {
  const items = list(data?.items).slice().sort((a, b) => text(a.releaseDate).localeCompare(text(b.releaseDate)));
  if (!items.length) {
    renderEmpty("radarrUpcomingList", "Rien de prévu", "Aucun film manquant ou à venir");
    return;
  }
  let lastBucket = "";
  const html = [];
  items.forEach((item) => {
    const bucket = dayBucket(item.releaseDate);
    if (bucket.key !== lastBucket) {
      lastBucket = bucket.key;
      html.push(`<div class="section-label">${escapeHtml(bucket.label)}</div>`);
    }
    html.push(`
      <div class="list-row">
        <div class="row-copy"><div class="row-title">${escapeHtml(item.title || "Film")}${item.year ? ` <span class="row-subtitle">(${escapeHtml(item.year)})</span>` : ""}</div><div class="row-subtitle">${escapeHtml(item.status || "Surveillé")}</div></div>
        <div class="row-meta"><strong>${escapeHtml(formatDate(item.releaseDate, { day: "2-digit", month: "short" }))}</strong>${reasonBadge(item)}</div>
      </div>
    `);
  });
  $("radarrUpcomingList").innerHTML = html.join("");
}

function statCard(label, value, percent = null, alert = false) {
  const width = percent == null ? "" : `<div class="stat-bar"><span style="width:${clamp(percent, 0, 100)}%"></span></div>`;
  return `<div class="stat${alert ? " alert" : ""}"><div class="stat-label">${escapeHtml(label)}</div><div class="stat-value">${escapeHtml(value)}</div>${width}</div>`;
}

function renderSystem(data) {
  const root = $("systemStats");
  const cards = [];
  const memoryTotal = Number(data?.memory?.totalBytes);
  const memoryUsed = Number(data?.memory?.usedBytes);
  if (Number.isFinite(memoryTotal) && memoryTotal > 0 && Number.isFinite(memoryUsed)) {
    const percent = Math.round(memoryUsed / memoryTotal * 100);
    cards.push(statCard("Mémoire", `${formatBytes(memoryUsed)} · ${percent} %`, percent, percent >= 90));
  }
  const down = data?.network?.rxBps;
  const up = data?.network?.txBps;
  cards.push(statCard("Réseau", `↓ ${formatSpeed(down)} · ↑ ${formatSpeed(up)}`));
  list(data?.disks).forEach((disk) => {
    if (disk.error) {
      cards.push(statCard(disk.label || "Disque", "Indisponible", null, true));
      return;
    }
    const total = Number(disk.total);
    const used = Number(disk.used);
    const percent = total > 0 ? Math.round(used / total * 100) : null;
    cards.push(statCard(disk.label || "Disque", `${formatBytes(disk.free)} libres`, percent, percent >= 95));
  });
  root.innerHTML = cards.join("") || `<div class="error-state">Statistiques système indisponibles.</div>`;
}

function renderNetworkHistory(data) {
  const root = $("systemHistory");
  if (!data?.configured) {
    root.innerHTML = statCard("Historique réseau", "qBittorrent non configuré");
    return;
  }
  if (Number(data.samples) < 2) {
    root.innerHTML = statCard("Historique réseau", `Collecte en cours · ${data.samples || 0} point`);
    return;
  }
  const cards = [];
  if (data.last24h) {
    cards.push(statCard("Téléchargé · 24 h", formatBytes(data.last24h.downloadBytes)));
    cards.push(statCard("Envoyé · 24 h", formatBytes(data.last24h.uploadBytes)));
  }
  if (data.last7d) {
    cards.push(statCard("Téléchargé · 7 j", formatBytes(data.last7d.downloadBytes)));
    cards.push(statCard("Envoyé · 7 j", formatBytes(data.last7d.uploadBytes)));
  }
  root.innerHTML = cards.join("") || statCard("Historique réseau", "Collecte en cours");
}

function renderLibrary(data) {
  const items = list(data?.items).slice(0, 6);
  if (!items.length) {
    renderEmpty("libraryToday", "Aucun import aujourd’hui", "Sonarr et Radarr sont à jour");
    return;
  }
  $("libraryToday").innerHTML = items.map((item) => `
    <div class="activity-item">
      <div class="activity-check">${icon("i-check")}</div>
      <div class="activity-copy"><strong title="${escapeHtml(item.title)}">${escapeHtml(item.title)}</strong><span>${escapeHtml(item.type)}</span></div>
      <span class="activity-time">${escapeHtml(formatTime(item.date))}</span>
    </div>
  `).join("");
}

function renderScans(data) {
  const sonarr = data?.sonarr ? formatTime(data.sonarr) : "—";
  const radarr = data?.radarr ? formatTime(data.radarr) : "—";
  $("scanLine").textContent = `Scans · Sonarr ${sonarr} · Radarr ${radarr}`;
}

function safeUrl(value) {
  try {
    const url = new URL(value, window.location.href);
    return ["http:", "https:"].includes(url.protocol) ? url.href : "#";
  } catch { return "#"; }
}

function renderLinks(data) {
  const links = list(data?.links);
  $("links").innerHTML = links.map((link) => `
    <a class="link-card" href="${escapeHtml(safeUrl(link.url))}" target="_blank" rel="noopener">
      <strong>${escapeHtml(link.label)}</strong><span>Ouvrir l’interface ↗</span>
    </a>
  `).join("") || `<div class="empty-state">Aucun accès rapide configuré.</div>`;
}

function weatherIcon(code, isDay = true) {
  const value = Number(code);
  if (value === 0) return isDay === false ? "🌙" : "☀️";
  if ([1, 2, 3].includes(value)) return "⛅";
  if ([45, 48].includes(value)) return "🌫️";
  if ((value >= 51 && value <= 67) || (value >= 80 && value <= 82)) return "🌦️";
  if ((value >= 71 && value <= 77) || (value >= 85 && value <= 86)) return "❄️";
  if (value >= 95) return "⛈️";
  return "☁️";
}

function renderWeather(data) {
  if (!data || data.configured === false || data.error) {
    $("weatherIcon").textContent = "—";
    $("weatherTemp").textContent = "—";
    $("weatherLabel").textContent = data?.configured === false ? "Non configurée" : "Indisponible";
    return;
  }
  $("weatherIcon").textContent = weatherIcon(data.code, data.isDay);
  $("weatherTemp").textContent = Number.isFinite(Number(data.tempC)) ? `${Math.round(Number(data.tempC))}°` : "—";
  $("weatherLabel").textContent = data.label || "Météo";
}

function renderWeatherModal(data) {
  const root = $("weatherContent");
  if (!data || data.configured === false || data.error) {
    root.innerHTML = `<div class="error-state"><div><strong>Météo indisponible</strong><span>Configurez WEATHER_LAT et WEATHER_LON.</span></div></div>`;
    return;
  }
  const current = data.current || {};
  $("weatherModalTitle").textContent = data.label || "Météo";
  const hours = list(data.hourly).filter((item) => Date.parse(item.time) >= Date.now() - 900000).filter((_, index) => index % 2 === 0).slice(0, 6);
  const days = list(data.daily).slice(0, 7);
  root.innerHTML = `
    <div class="weather-hero">
      <div class="weather-now"><span class="icon">${weatherIcon(current.code, current.isDay)}</span><div><strong>${Number.isFinite(Number(current.tempC)) ? `${Math.round(Number(current.tempC))}°C` : "—"}</strong><span>Précipitations ${Math.round(Number(current.precipProb) || 0)} %</span></div></div>
      <span class="eyebrow">${escapeHtml(data.timezone || "")}</span>
    </div>
    <div class="weather-section-title">Prochaines heures</div>
    <div class="hourly-grid">${hours.map((hour) => `<div class="hour-item"><span>${escapeHtml(formatTime(hour.time))}</span><b>${weatherIcon(hour.code, hour.isDay)}</b><strong>${Math.round(Number(hour.tempC) || 0)}°</strong><span>${Math.round(Number(hour.precipProb) || 0)} %</span></div>`).join("")}</div>
    <div class="weather-section-title">7 prochains jours</div>
    <div>${days.map((day) => `<div class="forecast-row"><strong>${escapeHtml(formatDate(day.date, { weekday: "long", day: "2-digit" }))}</strong><span>${weatherIcon(day.code)} ${Math.round(Number(day.precipProbMax) || 0)} %</span><span>${Math.round(Number(day.tMin) || 0)}° / ${Math.round(Number(day.tMax) || 0)}°</span></div>`).join("")}</div>
  `;
}

function jellyseerrStatus(status) {
  const value = Number(status);
  if (value >= 5) return { label: "Disponible", className: "good", available: true };
  if (value >= 4) return { label: "Partiel", className: "warn" };
  if (value >= 3) return { label: "Traitement", className: "warn" };
  if (value >= 2) return { label: "Demandé", className: "warn" };
  return { label: "Non demandé", className: "" };
}

function setMediaType(type) {
  state.mediaType = type === "movie" ? "movie" : "tv";
  $("addTypeTv").classList.toggle("active", state.mediaType === "tv");
  $("addTypeMovie").classList.toggle("active", state.mediaType === "movie");
  $("addQuery").placeholder = state.mediaType === "tv" ? "Rechercher une série…" : "Rechercher un film…";
  $("addResults").innerHTML = "";
  $("addHint").textContent = "Saisissez au moins 2 caractères.";
}

async function searchMedia() {
  const query = text($("addQuery").value);
  if (query.length < 2) {
    $("addHint").textContent = "Saisissez au moins 2 caractères.";
    return;
  }
  $("addHint").textContent = "Recherche en cours…";
  $("addResults").innerHTML = `<div class="loading-state">Interrogation de Jellyseerr…</div>`;
  try {
    const data = await api(`/api/jellyseerr/search?type=${encodeURIComponent(state.mediaType)}&query=${encodeURIComponent(query)}`);
    const items = list(data?.items);
    $("addHint").textContent = `${items.length} résultat${items.length > 1 ? "s" : ""}`;
    $("addResults").innerHTML = items.map((item) => {
      const status = jellyseerrStatus(item.status);
      return `
        <div class="result-row">
          <div><strong>${escapeHtml(item.title || "Média")} ${item.year ? `(${escapeHtml(item.year)})` : ""}</strong><span><span class="badge ${status.className}">${escapeHtml(status.label)}</span></span></div>
          <button type="button" data-media-id="${Number(item.mediaId)}" data-media-title="${escapeHtml(item.title)}" ${status.available ? "disabled" : ""}>${status.available ? "Déjà présent" : (state.mediaType === "tv" ? "Choisir" : "Demander")}</button>
        </div>
      `;
    }).join("") || `<div class="empty-state">Aucun résultat.</div>`;
  } catch (error) {
    $("addHint").textContent = "La recherche a échoué.";
    renderError("addResults", "Jellyseerr", error);
  }
}

async function openSeasons(mediaId, title) {
  state.currentTvId = Number(mediaId);
  state.selectedSeasons = new Set();
  $("seasonTitle").textContent = title || "Choisir les saisons";
  $("seasonHint").textContent = "Chargement des saisons…";
  $("seasonGrid").innerHTML = `<div class="loading-state">Chargement…</div>`;
  $("seasonRequestBtn").disabled = true;
  openDialog("seasonModal");
  try {
    const data = await api(`/api/jellyseerr/tv/${encodeURIComponent(mediaId)}`);
    const seasons = list(data?.seasons);
    $("seasonHint").textContent = seasons.length ? "Sélectionnez une ou plusieurs saisons." : "Aucune saison trouvée.";
    $("seasonGrid").innerHTML = seasons.map((season) => {
      const status = jellyseerrStatus(season.status);
      return `<button class="season-button" type="button" data-season="${Number(season.seasonNumber)}" ${status.available ? "disabled" : ""}><strong>Saison ${Number(season.seasonNumber)}</strong><span>${status.available ? "Déjà disponible" : `${Number(season.episodeCount) || "—"} épisodes`}</span></button>`;
    }).join("");
  } catch (error) {
    $("seasonHint").textContent = "Impossible de charger les saisons.";
    renderError("seasonGrid", "Jellyseerr", error);
  }
}

async function requestMovie(button, mediaId) {
  button.disabled = true;
  button.textContent = "Envoi…";
  try {
    await post("/api/jellyseerr/request", { mediaId: Number(mediaId), mediaType: "movie" });
    button.textContent = "Demandé";
    toast("Film demandé dans Jellyseerr");
  } catch (error) {
    button.disabled = false;
    button.textContent = "Réessayer";
    toast(`Demande impossible : ${text(error.message)}`, "error");
  }
}

async function requestSeasons() {
  const seasons = [...state.selectedSeasons].sort((a, b) => a - b);
  if (!Number.isFinite(state.currentTvId) || !seasons.length) return;
  const button = $("seasonRequestBtn");
  button.disabled = true;
  button.textContent = "Envoi…";
  try {
    await post("/api/jellyseerr/request", { mediaId: state.currentTvId, mediaType: "tv", seasons });
    toast("Saisons demandées dans Jellyseerr");
    closeDialog("seasonModal");
  } catch (error) {
    button.disabled = false;
    toast(`Demande impossible : ${text(error.message)}`, "error");
  } finally {
    button.textContent = "Envoyer la demande";
  }
}

async function openWeather() {
  openDialog("weatherModal");
  if (state.weatherForecast && Date.now() - state.weatherForecastAt < 300_000) {
    renderWeatherModal(state.weatherForecast);
    return;
  }
  $("weatherContent").innerHTML = `<div class="loading-state">Chargement des prévisions…</div>`;
  try {
    state.weatherForecast = await api("/api/weather/forecast?days=7");
    state.weatherForecastAt = Date.now();
    renderWeatherModal(state.weatherForecast);
  } catch (error) {
    renderWeatherModal(null);
  }
}

async function refreshAll({ force = false } = {}) {
  if (state.refreshing) return;
  state.refreshing = true;
  $("reloadBtn").classList.add("is-spinning");

  const tasks = [
    api(`/api/status${force ? "?refresh=true" : ""}`).then(renderStatus).catch((error) => {
      $("statusDot").className = "status-dot bad";
      $("statusText").textContent = "Supervision indisponible";
      $("statusDetail").textContent = text(error.message);
      $("overviewHeadline").textContent = "Impossible de joindre le dashboard";
    }),
    api("/api/qbittorrent/torrents?filter=active").then(renderQb).catch((error) => { $("qbCount").textContent = "!"; renderError("qbList", "qBittorrent", error); }),
    api("/api/jellyfin/latest?limit=8").then(renderJelly).catch((error) => renderError("jellyList", "Jellyfin", error)),
    api("/api/sonarr/upcoming?days=14&limit=24").then(renderSonarr).catch((error) => renderError("sonarrList", "Sonarr", error)),
    api("/api/sonarr/importing?limit=10").then(renderImporting).catch(() => renderImporting(null)),
    api("/api/radarr/soon?days_future=365&limit=24").then(renderRadarr).catch((error) => renderError("radarrUpcomingList", "Radarr", error)),
    api("/api/system").then(renderSystem).catch((error) => renderError("systemStats", "Système", error)),
    api("/api/library/today?limit=6").then(renderLibrary).catch((error) => renderError("libraryToday", "Historique", error)),
    api("/api/scans").then(renderScans).catch(() => { $("scanLine").textContent = "Derniers scans : indisponibles"; }),
    api("/api/links").then(renderLinks).catch(() => renderLinks(null)),
    api("/api/weather").then(renderWeather).catch(() => renderWeather(null)),
  ];

  if (state.systemMode === "history") {
    tasks.push(api("/api/network/history").then(renderNetworkHistory).catch((error) => renderError("systemHistory", "Historique réseau", error)));
  }

  await Promise.allSettled(tasks);
  const now = new Date();
  $("lastUpdated").textContent = `Actualisé à ${now.toLocaleTimeString("fr-FR", { hour: "2-digit", minute: "2-digit" })}`;
  state.refreshing = false;
  $("reloadBtn").classList.remove("is-spinning");
}

function setupInteractions() {
  document.querySelectorAll("[data-close]").forEach((button) => {
    button.addEventListener("click", () => closeDialog(button.dataset.close));
  });
  document.querySelectorAll("dialog").forEach((dialog) => {
    dialog.addEventListener("click", (event) => {
      const rect = dialog.getBoundingClientRect();
      const inside = event.clientX >= rect.left && event.clientX <= rect.right && event.clientY >= rect.top && event.clientY <= rect.bottom;
      if (!inside) dialog.close();
    });
  });

  $("servicesBtn").addEventListener("click", () => openDialog("servicesModal"));
  $("menuBtn").addEventListener("click", () => openDialog("menuPanel"));
  $("weatherPill").addEventListener("click", openWeather);
  $("refreshBtn").addEventListener("click", () => openDialog("refreshModal"));
  $("addMediaBtn").addEventListener("click", () => {
    openDialog("addModal");
    setTimeout(() => $("addQuery").focus(), 80);
  });
  $("reloadBtn").addEventListener("click", async () => {
    await refreshAll({ force: true });
    scheduleRefresh();
    toast("Données actualisées");
  });
  $("jellyRefreshBtn").addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.classList.add("is-spinning");
    try {
      await post("/api/jellyfin/refresh", {});
      toast("Scan Jellyfin lancé");
      setTimeout(() => refreshAll(), 2500);
    } catch (error) {
      toast(`Scan impossible : ${text(error.message)}`, "error");
    } finally {
      button.disabled = false;
      button.classList.remove("is-spinning");
    }
  });

  $("sysToggleBtn").addEventListener("click", async () => {
    state.systemMode = state.systemMode === "live" ? "history" : "live";
    $("sysToggleBtn").textContent = state.systemMode === "live" ? "Live" : "24 h / 7 j";
    $("systemStats").classList.toggle("hidden", state.systemMode !== "live");
    $("systemHistory").classList.toggle("hidden", state.systemMode !== "history");
    if (state.systemMode === "history") {
      $("systemHistory").innerHTML = `<div class="loading-state">Chargement de l’historique…</div>`;
      try { renderNetworkHistory(await api("/api/network/history")); }
      catch (error) { renderError("systemHistory", "Historique réseau", error); }
    }
  });

  $("refreshChoices").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-refresh-seconds]");
    if (!button) return;
    setRefreshSeconds(button.dataset.refreshSeconds);
    closeDialog("refreshModal");
    await refreshAll();
  });

  $("radarrUpcomingList").addEventListener("click", (event) => {
    if (!event.target.closest("[data-radarr-legend]")) return;
    $("legendList").innerHTML = `
      <div class="legend-item"><strong>🎥</strong><span>Sortie cinéma passée, sortie numérique inconnue</span></div>
      <div class="legend-item"><strong>📀</strong><span>Sortie physique encore à venir</span></div>
      <div class="legend-item"><strong>🔍</strong><span>Film surveillé et recherché par Radarr</span></div>
      <div class="legend-item"><strong>🌍</strong><span>Disponibilité ou région potentiellement limitée</span></div>`;
    openDialog("legendModal");
  });

  $("addTypeTv").addEventListener("click", () => setMediaType("tv"));
  $("addTypeMovie").addEventListener("click", () => setMediaType("movie"));
  $("addSearchForm").addEventListener("submit", (event) => { event.preventDefault(); searchMedia(); });
  $("addResults").addEventListener("click", async (event) => {
    const button = event.target.closest("[data-media-id]");
    if (!button || button.disabled) return;
    const mediaId = Number(button.dataset.mediaId);
    if (state.mediaType === "movie") await requestMovie(button, mediaId);
    else await openSeasons(mediaId, button.dataset.mediaTitle);
  });
  $("seasonGrid").addEventListener("click", (event) => {
    const button = event.target.closest("[data-season]");
    if (!button || button.disabled) return;
    const season = Number(button.dataset.season);
    if (state.selectedSeasons.has(season)) state.selectedSeasons.delete(season);
    else state.selectedSeasons.add(season);
    button.classList.toggle("active", state.selectedSeasons.has(season));
    $("seasonRequestBtn").disabled = state.selectedSeasons.size === 0;
  });
  $("seasonRequestBtn").addEventListener("click", requestSeasons);

  document.addEventListener("click", (event) => {
    if (event.target.closest("[data-retry]")) refreshAll({ force: true });
  });

  document.querySelectorAll("[data-mobile-action]").forEach((button) => {
    button.addEventListener("click", () => {
      const action = button.dataset.mobileAction;
      if (action === "status") openDialog("servicesModal");
      if (action === "request") openDialog("addModal");
      if (action === "links") openDialog("menuPanel");
      if (action === "refresh") refreshAll({ force: true }).then(scheduleRefresh);
    });
  });
}

async function main() {
  tickClock();
  setInterval(tickClock, 1000);
  setupInteractions();
  renderRefreshChoices();
  setMediaType("tv");

  try {
    const meta = await api("/api/meta");
    if (meta?.title) {
      document.title = meta.title;
      $("appTitle").textContent = meta.title;
    }
    const serverRefresh = Number(meta?.refreshSeconds);
    if (REFRESH_OPTIONS.some((option) => option.seconds === serverRefresh)) state.refreshSeconds = serverRefresh;
  } catch {}

  try {
    const stored = Number(localStorage.getItem("capyflixRefreshSeconds"));
    if (REFRESH_OPTIONS.some((option) => option.seconds === stored)) state.refreshSeconds = stored;
  } catch {}

  renderRefreshChoices();
  await refreshAll();
  scheduleRefresh();
}

main();
