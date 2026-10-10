const suggestionsBody = document.getElementById("suggestionsBody");
let dashboardLoadedOnce = false;
let lastAlertSignature = "";

function renderInsights({ suggestions, receipts, shortages }) {
  const container = document.getElementById("insightsGrid");
  if (!container) return;
  const positiveSuggestions = suggestions.filter(item => Number(item.suggested_quantity ?? 0) > 0);
  const activeShortages = shortages.filter(item => Number(item.missing_quantity ?? 0) > 0 || item.missing_quantity == null);
  const cards = [];

  if (activeShortages.length) {
    const qty = activeShortages.reduce((sum, item) => sum + (Number(item.missing_quantity) || 0), 0);
    const orderCodes = [...new Set(activeShortages.map(item => item.work_order_code).filter(Boolean))];
    cards.push({
      level: "high", kicker: "ATENCIÓN PRIORITARIA", title: `${activeShortages.length} línea(s) con faltante`,
      detail: `${qty ? `${qty} unidad(es) cuantificadas` : "Hay cantidades por confirmar"}${orderCodes.length ? ` en ${orderCodes.length} orden(es)` : ""}. Revisa primero las órdenes que bloquean trabajo.`,
      link: "Ver faltantes", href: "ordenes.html"
    });
  }

  if (receipts.length) {
    cards.push({
      level: "medium", kicker: "REVISIÓN MANUAL", title: `${receipts.length} recepción(es) sin relacionar`,
      detail: "Relaciona las compras con una pieza del catálogo para que el equipo pueda validar su aplicación.",
      link: "Resolver recepciones", href: "faltantes.html"
    });
  }

  if (positiveSuggestions.length) {
    const units = positiveSuggestions.reduce((sum, item) => sum + (Number(item.suggested_quantity) || 0), 0);
    const top = [...positiveSuggestions].sort((a, b) => Number(b.suggested_quantity || 0) - Number(a.suggested_quantity || 0))[0];
    cards.push({
      level: "medium", kicker: "REABASTO SUGERIDO", title: `${positiveSuggestions.length} pieza(s) por reabastecer`,
      detail: `${units} unidad(es) sugeridas en total. La mayor sugerencia es ${top.name || top.sku || `la pieza ${top.part_id ?? "sin ID"}`} (${Number(top.suggested_quantity) || 0} unidades).`,
      link: "Revisar sugerencias", href: "#suggestionsBody"
    });
  }

  if (!cards.length) {
    cards.push({ level: "low", kicker: "OPERACIÓN ESTABLE", title: "Sin alertas con los datos actuales", detail: "No se encontraron faltantes cuantificados ni recepciones pendientes. Actualiza los datos para revisar de nuevo.", link: "Actualizar datos", href: "#refreshDashboard" });
  }

  container.innerHTML = cards.map(card => `
    <article class="insight-card priority-${card.level}">
      <span class="insight-kicker">${escapeHTML(card.kicker)}</span>
      <h3>${escapeHTML(card.title)}</h3>
      <p>${escapeHTML(card.detail)}</p>
      <a class="inline-link" href="${escapeHTML(card.href)}">${escapeHTML(card.link)} →</a>
    </article>`).join("");

  container.querySelector('a[href="#refreshDashboard"]')?.addEventListener("click", event => {
    event.preventDefault();
    loadDashboard({ manual: true });
  });
}

function notifyOperationalAlerts({ suggestions, receipts, shortages }, manual) {
  const activeShortages = shortages.filter(item => Number(item.missing_quantity ?? 0) > 0 || item.missing_quantity == null);
  const positiveSuggestions = suggestions.filter(item => Number(item.suggested_quantity ?? 0) > 0);
  const signature = `${activeShortages.length}|${receipts.length}|${positiveSuggestions.length}`;
  if (!dashboardLoadedOnce) {
    if (activeShortages.length) showToast(`${activeShortages.length} línea(s) presentan faltantes. Revisa las órdenes prioritarias.`, "warning", "Faltantes detectados");
    if (receipts.length) showToast(`Hay ${receipts.length} recepción(es) que requieren relación manual.`, "warning", "Compras por resolver");
    if (!activeShortages.length && !receipts.length && positiveSuggestions.length) showToast(`Hay ${positiveSuggestions.length} sugerencia(s) de reabasto para revisar.`, "info", "Reabasto sugerido");
  } else if (manual && signature !== lastAlertSignature) {
    showToast("Los datos operativos cambiaron. Revisa las señales y prioridades del dashboard.", "info", "Resumen actualizado");
  } else if (manual) {
    showToast("El resumen se actualizó correctamente.", "success", "Datos actualizados");
  }
  lastAlertSignature = signature;
  dashboardLoadedOnce = true;
}

async function loadDashboard(options = {}) {
  const manual = Boolean(options.manual);
  const refreshButton = document.getElementById("refreshDashboard");
  if (refreshButton) { refreshButton.disabled = true; refreshButton.textContent = "Consultando…"; }
  showMessage("dashboardMessage", "Consultando el servicio de inventario…");
  try {
    const [suggestionsData, receiptsData, shortagesData] = await Promise.all([
      apiRequest("/reorder-suggestions"),
      apiRequest("/unmatched-receipts"),
      apiRequest("/shortages")
    ]);
    const suggestions = listFromResponse(suggestionsData);
    const receipts = listFromResponse(receiptsData);
    const shortages = listFromResponse(shortagesData);
    const dashboardData = { suggestions, receipts, shortages };

    document.getElementById("suggestionCount").textContent = suggestions.length;
    document.getElementById("receiptCount").textContent = receipts.length;
    document.getElementById("shortageCount").textContent = shortages.length;
    document.getElementById("shortagesBody").innerHTML = shortages.length ? shortages.map(item => `
      <tr>
        <td><span class="part-name">${escapeHTML(item.work_order_code || "Orden sin código")}</span><span class="subtext">Orden ${escapeHTML(item.work_order_id ?? "—")}</span></td>
        <td><span class="part-name">${escapeHTML(item.name || "Pieza sin nombre")}</span><span class="subtext">ID ${escapeHTML(item.part_id ?? "sin identificar")}</span></td>
        <td><span class="pill red">${item.missing_quantity == null ? "Por definir" : `${escapeHTML(item.missing_quantity)} unidades`}</span></td>
        <td><a class="inline-link" href="ordenes.html?code=${encodeURIComponent(item.work_order_code || "")}">Ver orden →</a></td>
      </tr>`).join("") : `<tr><td colspan="4" class="empty">No hay faltantes abiertos.</td></tr>`;

    suggestionsBody.innerHTML = suggestions.length ? suggestions.map(item => {
      const orders = Array.isArray(item.work_order_ids) ? item.work_order_ids.join(", ") : "—";
      const qty = Number(item.suggested_quantity ?? 0);
      const lowStock = [item.available, item.on_hand, item.current_stock].some(value => value !== undefined && value !== null && Number(value) <= Number(item.minimum_stock ?? item.min_stock ?? 0));
      const urgency = lowStock || qty > 0 ? "yellow" : "gray";
      return `<tr>
        <td><span class="subtext">ID ${escapeHTML(item.part_id ?? "—")}</span><span class="part-name">${escapeHTML(item.sku || "Sin SKU")}</span></td>
        <td><span class="part-name">${escapeHTML(item.name || "Pieza sin nombre")}</span>${lowStock ? '<span class="subtext">Stock crítico según los datos disponibles</span>' : ""}</td>
        <td><span class="pill ${urgency}">${escapeHTML(item.suggested_quantity ?? 0)} unidades</span></td>
        <td>${escapeHTML(orders || "Sin órdenes relacionadas")}</td>
        <td><a class="inline-link" href="inventario.html?part_id=${encodeURIComponent(item.part_id ?? "")}">Ver saldo →</a></td>
      </tr>`;
    }).join("") : `<tr><td colspan="5" class="empty">No hay sugerencias de reabasto vigentes.</td></tr>`;

    renderInsights(dashboardData);
    notifyOperationalAlerts(dashboardData, manual);
    showMessage("dashboardMessage", "Datos actualizados.", "success");
  } catch (error) {
    showMessage("dashboardMessage", `No se pudo cargar el resumen: ${error.message}. Verifica que FastAPI esté activo.`, "error");
    if (typeof showToast === "function") showToast(`No se pudo consultar el dashboard: ${error.message}`, "error", "Error de conexión", 7500);
    suggestionsBody.innerHTML = `<tr><td colspan="5" class="empty">No se pudo consultar la API.</td></tr>`;
    document.getElementById("shortagesBody").innerHTML = `<tr><td colspan="4" class="empty">No se pudieron consultar los faltantes.</td></tr>`;
    document.getElementById("shortageCount").textContent = "—";
    document.getElementById("insightsGrid").innerHTML = `<article class="insight-card priority-high"><span class="insight-kicker">SIN CONEXIÓN</span><h3>No fue posible generar recomendaciones</h3><p>Verifica que la API esté activa y vuelve a intentarlo.</p></article>`;
  } finally {
    if (refreshButton) { refreshButton.disabled = false; refreshButton.textContent = "↻ Actualizar datos"; }
  }
}

document.getElementById("refreshDashboard")?.addEventListener("click", () => loadDashboard({ manual: true }));
document.addEventListener("DOMContentLoaded", () => loadDashboard());
