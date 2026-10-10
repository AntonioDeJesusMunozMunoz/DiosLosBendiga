/* piso.js - Utilidades compartidas de la app de piso (Frontend 1).
 *
 *  - API:      misma máquina, puerto 8000 (http o https según como se abrió la página)
 *  - Escáner:  html5-qrcode, Code 128, cámara trasera, lectura continua sin teclear
 *  - Feedback: beep, vibración y pantalla verde/roja
 *  - scanCode: GET /scan?code=... dice si la etiqueta es ubicación, orden o pieza
 */
(function () {
  "use strict";

  // ------------------------------------------------------------------ API
  const API_BASE = /^https?:$/.test(location.protocol)
    ? `${location.protocol}//${location.hostname}:8000`
    : "http://localhost:8000";

  async function api(path, options = {}) {
    const init = { ...options, headers: { ...(options.headers || {}) } };
    if (options.body !== undefined && typeof options.body !== "string") {
      init.body = JSON.stringify(options.body);
      init.headers["Content-Type"] = "application/json";
    }
    let response;
    try {
      response = await fetch(API_BASE + path, init);
    } catch (e) {
      return { ok: false, status: 0, data: { detail: "No se pudo conectar con el servidor" } };
    }
    const text = await response.text();
    let data = null;
    try { data = text ? JSON.parse(text) : null; } catch { data = { detail: text }; }
    return { ok: response.ok, status: response.status, data };
  }

  const detail = (r) => (r.data && r.data.detail) || `Error ${r.status}`;

  async function scanCode(code) {
    return api(`/scan?code=${encodeURIComponent(code)}`);
  }

  // ------------------------------------------------------------- utilidades
  const $ = (sel) => document.querySelector(sel);
  const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" }[c]));
  const partLabel = (p) => (p.sku ? p.sku : `P-${p.part_id}`);

  // --------------------------------------------------------------- feedback
  let audio = null;
  function unlockAudio() {
    try {
      audio = audio || new (window.AudioContext || window.webkitAudioContext)();
      if (audio.state === "suspended") audio.resume();
    } catch { audio = null; }
  }
  function tone(freq, ms, when = 0) {
    if (!audio) return;
    const o = audio.createOscillator();
    const g = audio.createGain();
    o.frequency.value = freq;
    o.type = "square";
    g.gain.value = 0.08;
    o.connect(g).connect(audio.destination);
    const t = audio.currentTime + when;
    o.start(t);
    o.stop(t + ms / 1000);
  }
  function beep(kind) {
    if (kind === "ok") { tone(1320, 90); }
    else if (kind === "done") { tone(988, 90); tone(1480, 160, 0.1); }
    else { tone(220, 300); }
    try { navigator.vibrate && navigator.vibrate(kind === "error" ? [120, 60, 120] : 60); } catch { /* sin vibración */ }
  }

  let flashTimer = null;
  function flash(kind, title, text) {
    const el = $("#flash");
    if (!el) return;
    el.className = `flash ${kind} show`;
    el.innerHTML = `<div class="flash-icon">${kind === "ok" ? "✓" : "✕"}</div>
      <div class="flash-title">${esc(title)}</div><div class="flash-text">${esc(text || "")}</div>
      <div class="flash-hint">Toca para continuar</div>`;
    clearTimeout(flashTimer);
    flashTimer = setTimeout(() => el.classList.remove("show"), kind === "ok" ? 1800 : 4000);
    el.onclick = () => el.classList.remove("show");
  }

  function toast(text, kind = "info") {
    const el = $("#toast");
    if (!el) return;
    el.textContent = text;
    el.className = `toast ${kind} show`;
    clearTimeout(el._t);
    el._t = setTimeout(() => el.classList.remove("show"), 2200);
  }

  // ------------------------------------------------------- hoja para elegir
  function chooseFrom(title, options) {
    // options: [{label, sub, value}] -> Promise(value | null)
    return new Promise((resolve) => {
      const sheet = $("#sheet");
      sheet.innerHTML = `<div class="sheet-box" role="dialog" aria-label="${esc(title)}">
        <div class="sheet-title">${esc(title)}</div>
        <div class="sheet-list">${options.map((o, i) =>
          `<button class="sheet-opt" data-i="${i}"><strong>${esc(o.label)}</strong>${o.sub ? `<span>${esc(o.sub)}</span>` : ""}</button>`
        ).join("") || `<div class="empty">No hay opciones</div>`}</div>
        <button class="btn ghost sheet-cancel">Cancelar</button></div>`;
      sheet.classList.add("show");
      const close = (v) => { sheet.classList.remove("show"); sheet.innerHTML = ""; resolve(v); };
      sheet.querySelectorAll(".sheet-opt").forEach((b) =>
        b.addEventListener("click", () => close(options[Number(b.dataset.i)].value)));
      sheet.querySelector(".sheet-cancel").addEventListener("click", () => close(null));
      sheet.onclick = (e) => { if (e.target === sheet) close(null); };
    });
  }

  async function pickPart(matches) {
    if (matches.length === 1) return matches[0];
    beep("ok");
    return chooseFrom("Este código es de varias piezas. ¿Cuál es?",
      matches.map((m) => ({ label: m.name, sub: `${partLabel(m)} · id ${m.part_id}`, value: m })));
  }

  // ---------------------------------------------------------------- escáner
  const Scanner = {
    qr: null,
    running: false,
    lastCode: "",
    lastAt: 0,
    busy: false,
    onCode: null,

    async start(onCode) {
      this.onCode = onCode;
      unlockAudio();
      if (this.running) return;
      if (!window.Html5Qrcode) {
        flash("error", "Sin lector de códigos", "La librería del escáner no cargó. Revisa internet o js/vendor/html5-qrcode.min.js");
        return;
      }
      if (!window.isSecureContext) {
        flash("error", "La cámara necesita https", "Abre la página con https://<IP>:8000/app/...");
        return;
      }
      $("#camera").classList.add("on");
      this.qr = this.qr || new Html5Qrcode("reader", {
        formatsToSupport: [Html5QrcodeSupportedFormats.CODE_128],
        experimentalFeatures: { useBarCodeDetectorIfSupported: true },
        verbose: false,
      });
      try {
        await this.qr.start(
          { facingMode: "environment" },
          {
            fps: 12,
            // Recuadro angosto: una etiqueta a la vez (las hojas traen 14 etiquetas juntas)
            qrbox: (w, h) => ({ width: Math.floor(w * 0.8), height: Math.max(60, Math.floor(h * 0.25)) }),
            disableFlip: true,
          },
          (text) => this._hit(text),
          () => {}
        );
        this.running = true;
        $("#camBtn").textContent = "Detener cámara";
        $("#camBtn").classList.add("stop");
      } catch (e) {
        $("#camera").classList.remove("on");
        flash("error", "No se pudo abrir la cámara", "Da permiso de cámara al navegador y usa https.");
      }
    },

    async stop() {
      if (this.qr && this.running) {
        try { await this.qr.stop(); } catch { /* ya estaba detenida */ }
      }
      this.running = false;
      const cam = $("#camera");
      if (cam) cam.classList.remove("on");
      const b = $("#camBtn");
      if (b) { b.textContent = "Activar cámara"; b.classList.remove("stop"); }
    },

    toggle(onCode) { return this.running ? this.stop() : this.start(onCode); },

    // La cámara lee la misma etiqueta muchas veces por segundo mientras sigue enfrente.
    // Solo cuenta de nuevo cuando la etiqueta salió de la imagen (no se vio por GAP_MS).
    GAP_MS: 1200,

    async _hit(raw) {
      const code = String(raw).trim();
      const now = Date.now();
      if (!code) return;
      if (code === this.lastCode) {
        const stillInView = now - this.lastAt < this.GAP_MS;
        this.lastAt = now;
        if (stillInView) return;
      }
      if (this.busy) return;
      this.lastCode = code;
      this.lastAt = now;
      const lr = $("#lastRead");
      if (lr) lr.textContent = `Leído: ${code}`;
      this.busy = true;
      try { await this.onCode(code); } finally { this.busy = false; }
    },
  };

  // Fallback sin cámara: escribir el código (solo para cuando falla la cámara)
  function bindManual(onCode) {
    const form = $("#manual");
    if (!form) return;
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = form.querySelector("input");
      const code = input.value.trim();
      if (!code) return;
      unlockAudio();
      input.value = "";
      await onCode(code);
    });
  }

  window.addEventListener("pagehide", () => { Scanner.stop(); });

  window.Piso = { API_BASE, api, detail, scanCode, $, esc, partLabel, beep, flash, toast, chooseFrom, pickPart,
                  Scanner, bindManual, unlockAudio };
})();
