const el = (id) => document.getElementById(id);

function fmtBytes(n) {
  if (n == null || isNaN(n)) return "—";
  const u = ["B","KB","MB","GB","TB"];
  let i = 0;
  let v = Number(n);
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${u[i]}`;
}

function pct(used, total) {
  if (!total || total <= 0) return null;
  return Math.round((used / total) * 100);
}

function fmtSpeed(bps) {
  if (!bps) return "—";
  return `${fmtBytes(bps)}/s`;
}

function fmtEta(sec) {
  if (sec == null || sec < 0 || sec === 8640000) return "—";
  if (sec === 0) return "0s";
  const h = Math.floor(sec / 3600);
  const m = Math.floor((sec % 3600) / 60);
  if (h > 0) return `${h}h${m.toString().padStart(2,"0")}`;
  return `${m}m`;
}

function safeText(s) {
  return (s == null ? "" : String(s)).trim();
}

function toLocalShort(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  return d.toLocaleString(undefined, { weekday:"short", hour:"2-digit", minute:"2-digit" });
}

function toLocalDate(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (isNaN(d.getTime())) return "—";
  return d.toLocaleDateString(undefined, { weekday:"short", day:"2-digit", month:"short" });
}

function clockTick() {
  const d = new Date();
  el("clockTime").textContent = d.toLocaleTimeString(undefined, { hour:"2-digit", minute:"2-digit" });
  el("clockDate").textContent = d.toLocaleDateString(undefined, { weekday:"long", day:"2-digit", month:"long" });
}

function setStatus(ok, text) {
  const dot = el("statusDot");
  dot.classList.remove("good","bad");
  dot.classList.add(ok ? "good" : "bad");
  el("statusText").textContent = text;
}

async function jget(path) {
  const r = await fetch(path, { cache: "no-store" });
  if (!r.ok) throw new Error(`${path} -> ${r.status}`);
  return await r.json();
}

function renderSonarr(items) {
  const root = el("sonarrList");
  root.innerHTML = "";
  if (!items || items.length === 0) {
    root.innerHTML = `<div class="row"><div class="main"><div class="primary">Aucun épisode</div><div class="secondary">Rien de prévu</div></div><div class="meta"><span class="tag warn">—</span></div></div>`;
    return;
  }
  for (const it of items) {
    const series = safeText(it.seriesTitle) || "Série";
    const ep = safeText(it.episodeTitle) || "Épisode";
    const sn = it.seasonNumber ?? "?";
    const en = it.episodeNumber ?? "?";
    const when = toLocalShort(it.airDateUtc);
    const have = it.hasFile ? `<span class="tag good">OK</span>` : `<span class="tag warn">À venir</span>`;
    const div = document.createElement("div");
    div.className = "row";
    div.innerHTML = `
      <div class="main">
        <div class="primary">${series}</div>
        <div class="secondary">S${String(sn).padStart(2,"0")}E${String(en).padStart(2,"0")} — ${ep}</div>
      </div>
      <div class="meta">
        <div>${when}</div>
        <div style="margin-top:6px">${have}</div>
      </div>
    `;
    root.appendChild(div);
  }
}

function renderQb(items) {
  const root = el("qbList");
  root.innerHTML = "";
  if (!items || items.length === 0) {
    root.innerHTML = `<div class="row"><div class="main"><div class="primary">Aucune activité</div><div class="secondary">Téléchargements au repos</div></div><div class="meta"><span class="tag good">OK</span></div></div>`;
    return;
  }
  for (const t of items) {
    const name = safeText(t.name) || "Torrent";
    const pct = Math.max(0, Math.min(1, Number(t.progress ?? 0)));
    const dl = fmtSpeed(t.dlspeed);
    const ul = fmtSpeed(t.upspeed);
    const eta = fmtEta(t.eta);
    const state = safeText(t.state);
    const div = document.createElement("div");
    div.className = "row";
    div.innerHTML = `
      <div class="main">
        <div class="primary">${name}</div>
        <div class="secondary">${state || "—"} • DL ${dl} • UL ${ul} • ETA ${eta}</div>
        <div class="progress"><div style="width:${(pct*100).toFixed(1)}%"></div></div>
      </div>
      <div class="meta">
        <div class="tag">${Math.round(pct*100)}%</div>
      </div>
    `;
    root.appendChild(div);
  }
}

function renderJelly(items) {
  const root = el("jellyList");
  root.innerHTML = "";
  if (!items || items.length === 0) {
    root.innerHTML = `<div class="row"><div class="main"><div class="primary">Aucun ajout</div><div class="secondary">Jellyfin n’a rien renvoyé</div></div><div class="meta"><span class="tag warn">—</span></div></div>`;
    return;
  }
  for (const it of items) {
    const name = safeText(it.name) || "Média";
    const typ = safeText(it.type) || "Item";
    let sub = typ;
    if (typ.toLowerCase() === "episode") {
      const series = safeText(it.seriesName);
      const s = it.parentIndexNumber ?? "?";
      const e = it.indexNumber ?? "?";
      sub = `${series || "Série"} • S${String(s).padStart(2,"0")}E${String(e).padStart(2,"0")}`;
    } else if (it.productionYear) {
      sub = `${typ} • ${it.productionYear}`;
    }
    const img = it.id ? `/api/jellyfin/items/${encodeURIComponent(it.id)}/image?maxHeight=240&quality=80` : null;
    const div = document.createElement("div");
    div.className = "media";
    div.innerHTML = `
      <div class="poster">${img ? `<img loading="lazy" src="${img}" alt="">` : ""}</div>
      <div class="txt">
        <div class="name">${name}</div>
        <div class="sub">${sub}</div>
      </div>
    `;
    root.appendChild(div);
  }
}

function renderLinks(links) {
  const root = el("links");
  root.innerHTML = "";
  for (const l of (links || [])) {
    const a = document.createElement("a");
    a.className = "btn";
    a.href = l.url;
    a.target = "_blank";
    a.rel = "noopener";
    a.textContent = l.label;
    root.appendChild(a);
  }
}

function renderRadarrUpcoming(items) {
  const root = el("radarrUpcomingList");
  root.innerHTML = "";
  if (!items || items.length === 0) {
    root.innerHTML = `<div class="row"><div class="main"><div class="primary">Aucun film</div><div class="secondary">Rien de prévu</div></div><div class="meta"><span class="tag warn">—</span></div></div>`;
    return;
  }
  for (const it of items) {
    const title = safeText(it.title) || "Film";
    const year = it.year ? `(${it.year})` : "";
    const when = toLocalDate(it.releaseDate);
    const reason = safeText(it.reason) || "—";
    const tag = reason === "Queued" ? `<span class="tag good">Queue</span>` :
                reason === "Missing" ? `<span class="tag warn">Manquant</span>` :
                `<span class="tag warn">À venir</span>`;
    const div = document.createElement("div");
    div.className = "row";
    div.innerHTML = `
      <div class="main">
        <div class="primary">${title} ${year}</div>
        <div class="secondary">${reason}${safeText(it.status) ? ` • ${safeText(it.status)}` : ""}</div>
      </div>
      <div class="meta">
        <div>${when}</div>
        <div style="margin-top:6px">${tag}</div>
      </div>
    `;
    root.appendChild(div);
  }
}

function renderSystem(sys) {
  const root = el("systemStats");
  if (!root) return;
  root.innerHTML = "";

  const memTotal = sys?.memory?.totalBytes;
  const memUsed = sys?.memory?.usedBytes;
  const memAvail = sys?.memory?.availBytes;

  const stats = [];

  if (memTotal && memUsed != null) {
    const p = pct(memUsed, memTotal);
    stats.push({ k: "RAM", v: `${fmtBytes(memUsed)} / ${fmtBytes(memTotal)}${p != null ? ` (${p}%)` : ""}` });
  } else if (memTotal && memAvail != null) {
    const used = memTotal - memAvail;
    const p = pct(used, memTotal);
    stats.push({ k: "RAM", v: `${fmtBytes(used)} / ${fmtBytes(memTotal)}${p != null ? ` (${p}%)` : ""}` });
  }

  for (const d of (sys?.disks || [])) {
    if (d.error) {
      stats.push({ k: `Disque: ${d.label}`, v: "Erreur" });
      continue;
    }
    const p = pct(d.used, d.total);
    stats.push({
      k: `Disque: ${d.label}`,
      v: `${fmtBytes(d.free)} libre / ${fmtBytes(d.total)}${p != null ? ` (${p}% used)` : ""}`,
    });
  }

  if (stats.length === 0) {
    root.innerHTML = `<div class="stat"><div class="k">Système</div><div class="v">Indisponible</div></div>`;
    return;
  }

  for (const s of stats.slice(0, 6)) {
    const div = document.createElement("div");
    div.className = "stat";
    div.innerHTML = `<div class="k">${s.k}</div><div class="v">${s.v}</div>`;
    root.appendChild(div);
  }
}

function setupMenu() {
  const btn = el("menuBtn");
  const closeBtn = el("menuCloseBtn");
  const overlay = el("menuOverlay");
  const panel = el("menuPanel");
  if (!btn || !closeBtn || !overlay || !panel) return;

  const open = () => {
    overlay.classList.remove("hidden");
    panel.classList.remove("hidden");
    overlay.setAttribute("aria-hidden", "false");
    panel.setAttribute("aria-hidden", "false");
  };
  const close = () => {
    overlay.classList.add("hidden");
    panel.classList.add("hidden");
    overlay.setAttribute("aria-hidden", "true");
    panel.setAttribute("aria-hidden", "true");
  };

  btn.addEventListener("click", open);
  closeBtn.addEventListener("click", close);
  overlay.addEventListener("click", close);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") close();
  });
}

async function refreshAll() {
  // Ne pas bloquer tout le dashboard si 1 API tombe.
  const tasks = [
    jget("/api/status").then((status) => {
      const okCount = (status.items || []).filter(x => x.ok).length;
      const total = (status.items || []).length;
      setStatus(status.ok, `Services: ${okCount}/${total}`);
    }).catch(() => setStatus(false, "Erreur réseau / config")),

    jget("/api/sonarr/upcoming?days=7&limit=8")
      .then((sonarr) => renderSonarr(sonarr.items))
      .catch(() => renderSonarr([])),

    jget("/api/radarr/soon?days_future=365&limit=8")
      .then((radarr) => renderRadarrUpcoming(radarr.items))
      .catch(() => renderRadarrUpcoming([])),

    jget("/api/qbittorrent/torrents?filter=active&limit=6")
      .then((qb) => renderQb(qb.items))
      .catch(() => renderQb([])),

    jget("/api/jellyfin/latest?limit=9")
      .then((jelly) => renderJelly(jelly.items))
      .catch(() => renderJelly([])),

    jget("/api/system")
      .then((sys) => renderSystem(sys))
      .catch(() => renderSystem(null)),

    jget("/api/links")
      .then((links) => renderLinks(links.links))
      .catch(() => renderLinks([])),
  ];

  await Promise.allSettled(tasks);
}

async function main() {
  clockTick();
  setInterval(clockTick, 1000);
  setupMenu();

  let refreshSeconds = 45;
  try {
    const meta = await jget("/api/meta");
    if (meta && meta.title) {
      document.title = meta.title;
      const h1 = document.querySelector("h1");
      if (h1) h1.textContent = meta.title;
    }
    if (meta && Number.isFinite(Number(meta.refreshSeconds))) {
      refreshSeconds = Math.max(15, Math.min(300, Number(meta.refreshSeconds)));
    }
  } catch (e) {
    // keep defaults
  }
  el("refreshLabel").textContent = `${refreshSeconds}s`;

  await refreshAll();
  setInterval(refreshAll, refreshSeconds * 1000);
}

main();
