let currentPartId = null;
// Catálogo inicial de demostración. El contrato actual no expone GET /parts para listar piezas.
const demoParts = [
  { id: 1001, sku: "ROD-6204", name: "Rodamiento rígido 6204-2RS" },
  { id: 1002, sku: "RET-3552", name: "Retén radial 35x52x7 NBR" },
  { id: 1003, sku: "OR-028", name: "O-Ring Viton 28x2.5" },
  { id: 1004, sku: "TOR-M8-25", name: "Tornillo Allen M8x25 Grado 12.9" }
];
const params = new URLSearchParams(window.location.search);
const partInput = document.getElementById("partId");
if (params.get("part_id")) partInput.value = params.get("part_id");

function extractPartId(value) {
  const match = String(value || "").trim().match(/^(\d+)/);
  return match ? Number(match[1]) : NaN;
}

function resolvePartId(value) {
  const raw = String(value || "").trim();
  const leadingId = raw.match(/^(\d+)/);
  if (leadingId) return Number(leadingId[1]);
  const normalized = raw.toLocaleLowerCase("es-MX");
  const found = demoParts.find(part => part.sku.toLocaleLowerCase("es-MX") === normalized || part.name.toLocaleLowerCase("es-MX") === normalized || part.name.toLocaleLowerCase("es-MX").includes(normalized));
  return found ? found.id : NaN;
}

async function loadGeneralStock() {
  const body = document.getElementById("generalStockBody");
  if (!body) return;
  body.innerHTML = `<tr><td colspan="8" class="empty">Cargando existencias desde la API…</td></tr>`;
  const results = await Promise.all(demoParts.map(async part => {
    try {
      const stock = await apiRequest(`/parts/${part.id}/availability`);
      return { part, stock, error: null };
    } catch (error) {
      return { part, stock: null, error };
    }
  }));
  body.innerHTML = results.map(({part, stock, error}) => {
    if (error) return `<tr data-part-id="${part.id}">
      <td>${part.id}</td><td><strong>${escapeHTML(part.sku)}</strong></td><td>${escapeHTML(part.name)}</td>
      <td colspan="3"><span class="pill gray">No disponible</span><span class="subtext">No se pudo consultar el saldo</span></td>
      <td><span class="pill red">Error</span></td>
      <td><button class="button button-outline general-part-view" data-part-id="${part.id}" type="button">Reintentar</button></td>
    </tr>`;
    const onHand = Number(stock.on_hand ?? 0);
    const reserved = Number(stock.reserved ?? 0);
    const available = Number(stock.available ?? 0);
    const status = available <= 0 ? `<span class="pill red">Sin disponible</span>` : available <= 5 ? `<span class="pill yellow">Stock bajo</span>` : `<span class="pill green">Disponible</span>`;
    return `<tr data-part-id="${part.id}">
      <td>${part.id}</td><td><strong>${escapeHTML(part.sku)}</strong></td><td><span class="part-name">${escapeHTML(part.name)}</span></td>
      <td class="inline-number">${escapeHTML(onHand)}</td><td class="inline-number">${escapeHTML(reserved)}</td><td class="inline-number"><span class="pill ${available > 0 ? "green" : "red"}">${escapeHTML(available)}</span></td>
      <td>${status}</td><td><button class="button button-outline general-part-view" data-part-id="${part.id}" type="button">Ver detalle</button></td>
    </tr>`;
  }).join("");
  document.getElementById("partsCount").textContent = `${demoParts.length} piezas`;
  filterGeneralStock(null);
}


function filterGeneralStock(partId) {
  const rows = [...document.querySelectorAll("#generalStockBody tr[data-part-id]")];
  const showAllButton = document.getElementById("showAllParts");
  let visible = 0;
  rows.forEach(row => {
    const matches = partId === null || Number(row.dataset.partId) === Number(partId);
    row.hidden = !matches;
    if (matches) visible++;
  });
  if (showAllButton) showAllButton.hidden = partId === null;
  const count = document.getElementById("partsCount");
  if (count) count.textContent = partId === null ? `${rows.length} piezas` : `${visible} resultado${visible === 1 ? "" : "s"}`;
}

async function loadAvailability(partId) {
  showMessage("availabilityMessage", "Consultando existencias…");
  document.getElementById("locationsBody").innerHTML = `<tr><td colspan="5" class="empty">Consultando…</td></tr>`;
  try {
    const data = await apiRequest(`/parts/${encodeURIComponent(partId)}/availability`);
    currentPartId = Number(data.part_id);
    document.getElementById("onHand").textContent = data.on_hand;
    document.getElementById("reserved").textContent = data.reserved;
    document.getElementById("available").textContent = data.available;
    const locations = Array.isArray(data.locations) ? data.locations : [];
    document.getElementById("locationCount").textContent = `${locations.length} ubicaciones`;
    document.getElementById("locationsBody").innerHTML = locations.length ? locations.map(location => `
      <tr>
        <td><span class="part-name">${escapeHTML(location.code || `Ubicación ${location.location_id}`)}</span><span class="subtext">ID ${escapeHTML(location.location_id)}</span></td>
        <td class="inline-number">${escapeHTML(location.on_hand)}</td>
        <td class="inline-number">${escapeHTML(location.reserved)}</td>
        <td><span class="pill ${Number(location.available) > 0 ? "green" : "gray"}">${escapeHTML(location.available)}</span></td>
        <td><button class="button button-outline view-ledger" type="button">Ver Kardex</button></td>
      </tr>`).join("") : `<tr><td colspan="5" class="empty">No hay saldo por ubicación para esta pieza.</td></tr>`;
    showMessage("availabilityMessage", `Pieza ${data.part_id}: disponibilidad consultada.`, "success");
    showToast(`Se consultó la disponibilidad de la pieza ${data.part_id}.`, "success", "Existencias consultadas");
    document.getElementById("refreshLedger").disabled = false;
    await loadLedger(currentPartId);
    document.getElementById("ledgerSubtitle").textContent = `Movimientos de la pieza ID ${currentPartId}, del más reciente al más antiguo.`;
  } catch (error) {
    currentPartId = null;
    document.getElementById("onHand").textContent = "—";
    document.getElementById("reserved").textContent = "—";
    document.getElementById("available").textContent = "—";
    document.getElementById("locationCount").textContent = "— ubicaciones";
    document.getElementById("locationsBody").innerHTML = `<tr><td colspan="5" class="empty">No se pudo consultar la pieza.</td></tr>`;
    document.getElementById("refreshLedger").disabled = true;
    showMessage("availabilityMessage", `Error al consultar existencias: ${error.message}`, "error");
    showToast(`No se pudo consultar la disponibilidad: ${error.message}`, "error", "Error de inventario", 7500);
  }
}
async function loadLedger(partId) {
  if (!partId) return;
  showMessage("ledgerMessage", ""); // No existe un mensaje independiente en esta sección; se mantiene la tabla como feedback.
  document.getElementById("ledgerBody").innerHTML = `<tr><td colspan="5" class="empty">Cargando Kardex…</td></tr>`;
  try {
    const data = await apiRequest(`/parts/${encodeURIComponent(partId)}/ledger`);
    const entries = Array.isArray(data.entries) ? data.entries : [];
    document.getElementById("ledgerBody").innerHTML = entries.length ? entries.map(entry => {
      const quantity = Number(entry.quantity);
      const quantityLabel = `${quantity > 0 ? "+" : ""}${quantity}`;
      const pillClass = quantity > 0 ? "green" : quantity < 0 ? "red" : "gray";
      return `<tr>
        <td>${escapeHTML(formatDate(entry.occurred_at))}</td>
        <td><span class="pill ${pillClass}">${escapeHTML(movementLabel(entry.type))}</span></td>
        <td class="inline-number">${escapeHTML(quantityLabel)}</td>
        <td>${escapeHTML(entry.reference || "—")}<span class="subtext">Movimiento ${escapeHTML(entry.movement_id)}</span></td>
        <td class="inline-number">${escapeHTML(entry.balance)}</td>
      </tr>`;
    }).join("") : `<tr><td colspan="5" class="empty">No hay movimientos para esta pieza.</td></tr>`;
  } catch (error) {
    document.getElementById("ledgerBody").innerHTML = `<tr><td colspan="5" class="empty">No se pudo cargar el Kardex: ${escapeHTML(error.message)}</td></tr>`;
    showToast(`No se pudo cargar el Kardex: ${error.message}`, "error", "Error de Kardex", 7500);
  }
}
document.getElementById("availabilityForm")?.addEventListener("submit", event => {
  event.preventDefault();
  const partId = resolvePartId(partInput.value);
  if (!Number.isInteger(partId) || partId < 1) {
    showMessage("availabilityMessage", "No reconocí esa pieza. Selecciona una sugerencia o usa un ID, SKU o nombre del catálogo.", "error");
    return;
  }
  filterGeneralStock(partId);
  loadAvailability(partId);
  document.getElementById("generalStockSection")?.scrollIntoView({ behavior: "smooth", block: "start" });
});
document.getElementById("showAllParts")?.addEventListener("click", () => filterGeneralStock(null));
document.getElementById("locationsBody")?.addEventListener("click", event => {
  if (!event.target.closest(".view-ledger") || !currentPartId) return;
  document.getElementById("ledgerSection").scrollIntoView({ behavior: "smooth", block: "start" });
  loadLedger(currentPartId);
});
document.getElementById("refreshLedger")?.addEventListener("click", () => loadLedger(currentPartId));
document.getElementById("generalStockBody")?.addEventListener("click", event => {
  const button = event.target.closest(".general-part-view");
  if (!button) return;
  const partId = Number(button.dataset.partId);
  const part = demoParts.find(item => item.id === partId);
  if (part) partInput.value = `${part.id} — ${part.sku} — ${part.name}`;
  filterGeneralStock(partId);
  loadAvailability(partId);
  document.getElementById("availabilityForm").scrollIntoView({ behavior: "smooth", block: "start" });
});

document.addEventListener("DOMContentLoaded", () => {
  loadGeneralStock();
  if (params.get("part_id") && /^\d+$/.test(params.get("part_id"))) loadAvailability(Number(params.get("part_id")));
});
