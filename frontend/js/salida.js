/* salida.js - Pantalla 1: salida de refacciones contra orden (POST /issues). */
(function () {
  "use strict";
  const { api, detail, scanCode, $, esc, partLabel, beep, flash, toast, chooseFrom, pickPart, Scanner, bindManual } = window.Piso;

  const st = { order: null, location: null, part: null, qty: 1, lines: [], stockHere: null };

  // ------------------------------------------------------------ escaneo
  async function onCode(code) {
    const r = await scanCode(code);
    if (!r.ok) {
      beep("error");
      flash("error", "Código no reconocido", r.status === 404 ? code : detail(r));
      return;
    }
    const d = r.data;
    if (d.type === "work_order") {
      beep("ok");
      await setOrder({ code: d.code, work_order_id: d.work_order_id });
      toast(`Orden ${d.code}`, "ok");
    } else if (d.type === "location") {
      if (st.location && st.location.location_id === d.location_id) return;   // misma ubicación otra vez
      beep("ok");
      st.location = { location_id: d.location_id, code: d.code };
      toast(`Ubicación ${d.code}`, "ok");
      await loadStock();
    } else if (d.type === "part") {
      const p = await pickPart(d.matches);
      if (!p) return;
      beep("ok");
      await setPart(p);
      toast(`Pieza ${partLabel(p)}`, "ok");
    }
    render();
  }

  // -------------------------------------------------------------- estado
  async function setOrder(order) {
    st.order = order;
    await loadMaterials();
  }

  async function setPart(p) {
    st.part = p;
    const reserved = st.lines.filter((l) => l.part_id === p.part_id).reduce((s, l) => s + (l.reserved || 0), 0);
    st.qty = reserved > 0 ? reserved : 1;
    await loadStock();
  }

  async function loadMaterials() {
    st.lines = [];
    if (!st.order) return;
    const r = await api(`/work-orders/${encodeURIComponent(st.order.code)}/materials`);
    if (r.ok) st.lines = r.data.lines || [];
  }

  async function loadStock() {
    st.stockHere = null;
    if (!st.part || !st.location) return;
    const r = await api(`/parts/${st.part.part_id}/availability`);
    if (!r.ok) return;
    const here = (r.data.locations || []).find((l) => l.location_id === st.location.location_id);
    st.stockHere = { onHand: here ? here.on_hand : 0, available: r.data.available };
  }

  // ------------------------------------------------------------- dibujo
  function render() {
    $("#valOrder").textContent = st.order ? st.order.code : "Escanea o elige de la lista";
    $("#valLocation").textContent = st.location ? st.location.code : "Escanea la etiqueta del estante (ej. U-100)";
    $("#valPart").textContent = st.part
      ? (st.part.sku ? `${st.part.sku} · ${st.part.name}` : st.part.name)
      : "Escanea la etiqueta de la pieza";
    $("#stockInfo").textContent = st.stockHere
      ? `Hay ${st.stockHere.onHand} en ${st.location.code}`
      : "";
    $("#qtyValue").textContent = st.qty;

    const next = !st.order ? "stepOrder" : !st.location ? "stepLocation" : !st.part ? "stepPart" : null;
    for (const [id, v] of [["stepOrder", st.order], ["stepLocation", st.location], ["stepPart", st.part]]) {
      const el = $("#" + id);
      el.classList.toggle("done", !!v);
      el.classList.toggle("next", id === next);
    }
    $("#confirmBtn").disabled = !(st.order && st.location && st.part && st.qty >= 1);

    const box = $("#materials");
    box.hidden = !st.order;
    if (st.order) {
      $("#matCode").textContent = st.order.code;
      $("#matLines").innerHTML = st.lines.length ? st.lines.map((l, i) => {
        const kind = l.part_id == null ? "grey" : l.missing ? "red" : l.reserved ? "green" : l.issued ? "blue" : "grey";
        const parts = [];
        if (l.reserved) parts.push(`Reservado ${l.reserved}`);
        if (l.issued) parts.push(`Entregado ${l.issued}`);
        if (l.missing) parts.push(`Falta ${l.missing}`);
        if (l.missing === null) parts.push("Falta (sin cantidad)");
        if (l.part_id == null) parts.push("Pieza sin identificar");
        return `<button class="mat ${kind}" data-i="${i}" type="button" ${l.part_id && l.reserved ? "" : "disabled"}>
          <span class="mat-name">${esc(l.name)}</span><span class="mat-info">${esc(parts.join(" · ") || "Sin movimiento")}</span></button>`;
      }).join("") : `<div class="empty">Esta orden no tiene material aprobado todavía.</div>`;
    }
  }

  // ------------------------------------------------------------ acciones
  async function confirm() {
    const btn = $("#confirmBtn");
    btn.disabled = true;
    btn.textContent = "Enviando…";
    const body = {
      work_order_code: st.order.code, part_id: st.part.part_id,
      location_id: st.location.location_id, quantity: st.qty, issued_by: "app-piso",
    };
    const r = await api("/issues", { method: "POST", body });
    btn.textContent = "Confirmar salida";
    if (r.ok) {
      beep("done");
      const name = st.part.name;
      await loadStock();
      const left = st.stockHere ? ` · Quedan ${st.stockHere.onHand} en ${st.location.code}` : "";
      flash("ok", "Salida registrada", `${st.qty} × ${name} para ${st.order.code}${left}`);
      st.part = null;
      st.qty = 1;
      st.stockHere = null;
      await loadMaterials();
    } else if (r.status === 409) {
      beep("error");
      flash("error", "No se puede entregar", `Saldo insuficiente en esa ubicación. ${detail(r)}`);
    } else {
      beep("error");
      flash("error", `Error ${r.status || ""}`.trim(), detail(r));
    }
    render();
  }

  async function chooseOrder() {
    Piso.unlockAudio();
    const r = await api("/work-orders");
    if (!r.ok) { beep("error"); flash("error", "No se pudieron cargar las órdenes", detail(r)); return; }
    const o = await chooseFrom("Elige la orden de trabajo",
      r.data.items.map((w) => ({ label: w.code, sub: w.stage ? `Etapa: ${w.stage}` : "", value: w })));
    if (!o) return;
    await setOrder({ code: o.code, work_order_id: o.work_order_id });
    if (st.part) await setPart(st.part);
    render();
  }

  // ------------------------------------------------------------- eventos
  $("#camBtn").addEventListener("click", () => Scanner.toggle(onCode));
  $("#pickOrder").addEventListener("click", chooseOrder);
  $("#confirmBtn").addEventListener("click", confirm);
  $("#qtyMinus").addEventListener("click", () => { st.qty = Math.max(1, st.qty - 1); render(); });
  $("#qtyPlus").addEventListener("click", () => { st.qty += 1; render(); });
  document.querySelectorAll("[data-clear]").forEach((b) => b.addEventListener("click", () => {
    const k = b.dataset.clear;
    st[k] = null;
    if (k === "order") st.lines = [];
    if (k !== "order") st.stockHere = null;
    render();
  }));
  $("#matLines").addEventListener("click", async (e) => {
    const b = e.target.closest(".mat");
    if (!b || b.disabled) return;
    const l = st.lines[Number(b.dataset.i)];
    await setPart({ part_id: l.part_id, sku: null, name: l.name });
    beep("ok");
    render();
  });
  bindManual(async (code) => { await onCode(code); });

  render();
})();
