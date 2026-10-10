/* conteo.js - Pantalla 3: conteo cíclico en estante (POST /counts). */
(function () {
  "use strict";
  const { api, detail, scanCode, $, esc, partLabel, beep, flash, toast, pickPart, Scanner, bindManual } = window.Piso;

  const st = { location: null, lines: [], lastIdx: -1 };   // lines: [{part, counted}]

  async function onCode(code) {
    const r = await scanCode(code);
    if (!r.ok) {
      beep("error");
      flash("error", "Código no reconocido", r.status === 404 ? code : detail(r));
      return;
    }
    const d = r.data;
    if (d.type === "location") {
      if (st.location && st.location.location_id === d.location_id) return;   // el mismo estante otra vez: nada que hacer
      if (st.location && st.location.location_id !== d.location_id && st.lines.length) {
        beep("error");
        flash("error", "Conteo sin guardar", `Guarda o borra el conteo de ${st.location.code} antes de cambiar de estante.`);
        return;
      }
      st.location = { location_id: d.location_id, code: d.code };
      beep("ok");
      toast(`Estante ${d.code}`, "ok");
      $("#result").hidden = true;
    } else if (d.type === "part") {
      if (!st.location) {
        beep("error");
        flash("error", "Primero el estante", "Escanea la ubicación antes de las piezas.");
        return;
      }
      const p = await pickPart(d.matches);
      if (!p) return;
      const line = st.lines.find((l) => l.part.part_id === p.part_id);
      if (line) line.counted += 1;
      else st.lines.push({ part: p, counted: 1 });
      st.lastIdx = st.lines.findIndex((l) => l.part.part_id === p.part_id);
      beep("ok");
      toast(`${partLabel(p)}: ${line ? line.counted : 1}`, "ok");
    } else {
      beep("error");
      flash("error", "Aquí no se usan órdenes", "Escanea el estante y las piezas.");
      return;
    }
    render();
  }

  function reasonText() { return $("#reason").value.trim(); }

  function render() {
    $("#valLocation").textContent = st.location ? st.location.code : "Escanea la etiqueta del estante (ej. U-102)";
    $("#stepLocation").classList.toggle("done", !!st.location);
    $("#stepLocation").classList.toggle("next", !st.location);
    const units = st.lines.reduce((s, l) => s + l.counted, 0);
    $("#valParts").textContent = st.lines.length
      ? `${st.lines.length} pieza(s) · ${units} unidad(es)`
      : st.location ? "Escanea las piezas del estante" : "Primero escanea el estante";
    $("#stepParts").classList.toggle("done", st.lines.length > 0);
    $("#stepParts").classList.toggle("next", !!st.location && !st.lines.length);
    $("#lineCount").textContent = st.lines.length ? `(${st.lines.length})` : "";
    $("#lines").innerHTML = st.lines.length ? st.lines.map((l, i) => `
      <div class="count-line${i === st.lastIdx ? " bump" : ""}">
        <div class="count-name"><strong>${esc(partLabel(l.part))}</strong><span>${esc(l.part.name)}</span></div>
        <button class="qty-btn sm" data-act="minus" data-i="${i}" type="button" aria-label="Menos">−</button>
        <span class="qty-value sm">${l.counted}</span>
        <button class="qty-btn sm" data-act="plus" data-i="${i}" type="button" aria-label="Más">+</button>
        <button class="clear" data-act="del" data-i="${i}" type="button" aria-label="Quitar">✕</button>
      </div>`).join("") : `<div class="empty">Escanea piezas para agregarlas.</div>`;
    st.lastIdx = -1;
    document.querySelectorAll("#reasonChips .chip").forEach((c) => c.classList.toggle("on", c.textContent === reasonText()));
    $("#confirmBtn").disabled = !(st.location && st.lines.length && reasonText());
  }

  async function confirm() {
    if (!reasonText()) { beep("error"); flash("error", "Falta el motivo", "El motivo del conteo es obligatorio."); return; }
    const btn = $("#confirmBtn");
    btn.disabled = true;
    btn.textContent = "Guardando…";
    const r = await api("/counts", {
      method: "POST",
      body: { location_id: st.location.location_id, reason: reasonText(),
              lines: st.lines.map((l) => ({ part_id: l.part.part_id, counted_quantity: l.counted })) },
    });
    btn.textContent = "Guardar ajuste";
    if (r.ok) {
      beep("done");
      const adj = r.data.adjustments || [];
      const names = Object.fromEntries(st.lines.map((l) => [l.part.part_id, partLabel(l.part)]));
      flash("ok", "Conteo guardado", adj.length ? `${adj.length} ajuste(s) en ${st.location.code}` : "Sin diferencias");
      const res = $("#result");
      res.hidden = false;
      res.innerHTML = `<h2>Resultado en ${esc(st.location.code)}</h2>` + (adj.length
        ? adj.map((a) => `<div class="adj ${a.delta > 0 ? "up" : "down"}"><span>${esc(names[a.part_id] || "Pieza " + a.part_id)}</span>
            <strong>${a.delta > 0 ? "+" : ""}${a.delta}</strong></div>`).join("")
        : `<div class="empty">Todo cuadra con el sistema.</div>`);
      st.lines = [];
    } else {
      beep("error");
      flash("error", `Error ${r.status || ""}`.trim(), detail(r));
    }
    render();
  }

  $("#camBtn").addEventListener("click", () => Scanner.toggle(onCode));
  $("#confirmBtn").addEventListener("click", confirm);
  $("#reason").addEventListener("input", render);
  $("#reasonChips").addEventListener("click", (e) => {
    const c = e.target.closest(".chip");
    if (!c) return;
    $("#reason").value = c.textContent;
    render();
  });
  $("#lines").addEventListener("click", (e) => {
    const b = e.target.closest("[data-act]");
    if (!b) return;
    const i = Number(b.dataset.i);
    if (b.dataset.act === "plus") st.lines[i].counted += 1;
    if (b.dataset.act === "minus") st.lines[i].counted = Math.max(0, st.lines[i].counted - 1);
    if (b.dataset.act === "del") st.lines.splice(i, 1);
    render();
  });
  document.querySelectorAll("[data-clear]").forEach((b) => b.addEventListener("click", () => {
    if (st.lines.length) {
      flash("error", "Hay piezas contadas", "Quita las piezas o guarda el conteo antes de cambiar de estante.");
      return;
    }
    st.location = null;
    render();
  }));
  bindManual(async (code) => { await onCode(code); });

  render();
})();
