/* transferencias.js - Pantalla 2: mover piezas entre estantes (POST /transfers). */
(function () {
  "use strict";
  const { api, detail, scanCode, $, partLabel, beep, flash, toast, pickPart, Scanner, bindManual } = window.Piso;

  const st = { from: null, to: null, part: null, qty: 1, stockFrom: null };

  async function onCode(code) {
    const r = await scanCode(code);
    if (!r.ok) {
      beep("error");
      flash("error", "Código no reconocido", r.status === 404 ? code : detail(r));
      return;
    }
    const d = r.data;
    if (d.type === "location") {
      const loc = { location_id: d.location_id, code: d.code };
      if (!st.from) {
        st.from = loc;
        toast(`Origen ${d.code}`, "ok");
      } else if (!st.to || st.to.location_id !== loc.location_id) {
        if (loc.location_id === st.from.location_id) {
          beep("error");
          flash("error", "Mismo estante", "El destino debe ser distinto del origen.");
          return;
        }
        st.to = loc;
        toast(`Destino ${d.code}`, "ok");
      }
      beep("ok");
      await loadStock();
    } else if (d.type === "part") {
      const p = await pickPart(d.matches);
      if (!p) return;
      beep("ok");
      st.part = p;
      st.qty = 1;
      toast(`Pieza ${partLabel(p)}`, "ok");
      await loadStock();
    } else {
      beep("error");
      flash("error", "Aquí no se usan órdenes", "Escanea ubicaciones y piezas.");
      return;
    }
    render();
  }

  async function loadStock() {
    st.stockFrom = null;
    if (!st.part || !st.from) return;
    const r = await api(`/parts/${st.part.part_id}/availability`);
    if (!r.ok) return;
    const here = (r.data.locations || []).find((l) => l.location_id === st.from.location_id);
    st.stockFrom = here ? here.on_hand : 0;
  }

  function render() {
    $("#valFrom").textContent = st.from ? st.from.code : "Escanea de dónde sale (ej. U-100)";
    $("#valTo").textContent = st.to ? st.to.code : "Escanea a dónde va (ej. U-105)";
    $("#valPart").textContent = st.part ? `${partLabel(st.part)} · ${st.part.name}` : "Escanea la etiqueta de la pieza";
    $("#stockInfo").textContent = st.stockFrom !== null ? `Hay ${st.stockFrom} en ${st.from.code}` : "";
    $("#qtyValue").textContent = st.qty;
    $("#allBtn").hidden = !(st.stockFrom > 1);
    if (st.stockFrom > 1) $("#allBtn").textContent = `Mover todo (${st.stockFrom})`;

    const next = !st.from ? "stepFrom" : !st.to ? "stepTo" : !st.part ? "stepPart" : null;
    for (const [id, v] of [["stepFrom", st.from], ["stepTo", st.to], ["stepPart", st.part]]) {
      $("#" + id).classList.toggle("done", !!v);
      $("#" + id).classList.toggle("next", id === next);
    }
    $("#confirmBtn").disabled = !(st.from && st.to && st.part && st.qty >= 1);
  }

  async function confirm() {
    const btn = $("#confirmBtn");
    btn.disabled = true;
    btn.textContent = "Enviando…";
    const r = await api("/transfers", {
      method: "POST",
      body: { part_id: st.part.part_id, from_location_id: st.from.location_id,
              to_location_id: st.to.location_id, quantity: st.qty },
    });
    btn.textContent = "Transferir";
    if (r.ok) {
      beep("done");
      flash("ok", "Transferencia hecha", `${st.qty} × ${st.part.name}: ${st.from.code} → ${st.to.code}`);
      st.part = null;
      st.qty = 1;
      st.stockFrom = null;
    } else if (r.status === 409) {
      beep("error");
      flash("error", "No alcanza", `Saldo insuficiente en ${st.from.code}. ${detail(r)}`);
    } else {
      beep("error");
      flash("error", `Error ${r.status || ""}`.trim(), detail(r));
    }
    render();
  }

  $("#camBtn").addEventListener("click", () => Scanner.toggle(onCode));
  $("#confirmBtn").addEventListener("click", confirm);
  $("#qtyMinus").addEventListener("click", () => { st.qty = Math.max(1, st.qty - 1); render(); });
  $("#qtyPlus").addEventListener("click", () => { st.qty += 1; render(); });
  $("#allBtn").addEventListener("click", () => { if (st.stockFrom > 0) { st.qty = st.stockFrom; render(); } });
  document.querySelectorAll("[data-clear]").forEach((b) => b.addEventListener("click", async () => {
    st[b.dataset.clear] = null;
    await loadStock();
    render();
  }));
  bindManual(async (code) => { await onCode(code); });

  render();
})();
