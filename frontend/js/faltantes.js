const receiptsBody = document.getElementById("receiptsBody");
const resolveDialog = document.getElementById("resolveDialog");
let receiptsCache = [];

async function loadReceipts() {
  showMessage("receiptsMessage", "Consultando recepciones sin relacionar…");
  receiptsBody.innerHTML = `<tr><td colspan="6" class="empty">Cargando…</td></tr>`;
  try {
    const data = await apiRequest("/unmatched-receipts");
    receiptsCache = listFromResponse(data);
    document.getElementById("receiptListCount").textContent = `${receiptsCache.length} pendientes`;
    receiptsBody.innerHTML = receiptsCache.length ? receiptsCache.map(item => `
      <tr>
        <td><span class="part-name">${escapeHTML(item.part_number || "Sin número de parte")}</span><span class="subtext">Recepción ${escapeHTML(item.id)} · Línea ${escapeHTML(item.purchase_line_id)}</span></td>
        <td>${escapeHTML(item.description)}</td>
        <td><span class="inline-number">${escapeHTML(item.quantity)}</span></td>
        <td>${escapeHTML(formatDate(item.received_at))}</td>
        <td>${escapeHTML(item.work_order_code || "—")}</td>
        <td><button class="button receipt-resolve" type="button" data-receipt-id="${escapeHTML(item.id)}">Resolver →</button></td>
      </tr>`).join("") : `<tr><td colspan="6" class="empty">No hay compras pendientes de relacionar.</td></tr>`;
    showMessage("receiptsMessage", "Bandeja actualizada.", "success");
  } catch (error) {
    document.getElementById("receiptListCount").textContent = "— pendientes";
    receiptsBody.innerHTML = `<tr><td colspan="6" class="empty">No se pudo cargar la bandeja.</td></tr>`;
    showMessage("receiptsMessage", `Error al consultar recepciones: ${error.message}`, "error");
    showToast(`No se pudo cargar la bandeja de compras: ${error.message}`, "error", "Error de consulta", 7500);
  }
}
receiptsBody?.addEventListener("click", event => {
  const button = event.target.closest("[data-receipt-id]");
  if (!button) return;
  const receipt = receiptsCache.find(item => String(item.id) === button.dataset.receiptId);
  if (!receipt) return;
  document.getElementById("receiptId").value = receipt.id;
  document.getElementById("catalogPartId").value = "";
  document.getElementById("locationId").value = "";
  document.getElementById("receiptSummary").innerHTML = `<strong>${escapeHTML(receipt.description)}</strong><span class="subtext">Número de parte: ${escapeHTML(receipt.part_number || "—")} · Cantidad: ${escapeHTML(receipt.quantity)} · Línea: ${escapeHTML(receipt.purchase_line_id)}</span>`;
  showMessage("resolveMessage", "");
  resolveDialog.showModal();
});
function closeResolveDialog() { if (resolveDialog.open) resolveDialog.close(); }
document.getElementById("closeDialog")?.addEventListener("click", closeResolveDialog);
document.getElementById("cancelResolve")?.addEventListener("click", closeResolveDialog);
document.getElementById("resolveForm")?.addEventListener("submit", async event => {
  event.preventDefault();
  const receiptId = document.getElementById("receiptId").value;
  const partId = Number(document.getElementById("catalogPartId").value);
  const locationValue = document.getElementById("locationId").value.trim();
  if (!Number.isInteger(partId) || partId < 1) {
    showMessage("resolveMessage", "Introduce un ID numérico válido del catálogo.", "error");
    return;
  }
  const payload = { part_id: partId };
  if (locationValue) {
    const locationId = Number(locationValue);
    if (!Number.isInteger(locationId) || locationId < 1) {
      showMessage("resolveMessage", "El ID de ubicación debe ser un entero positivo.", "error");
      return;
    }
    payload.location_id = locationId;
  }
  const submitButton = event.submitter;
  if (submitButton) { submitButton.disabled = true; submitButton.textContent = "Resolviendo…"; }
  try {
    await apiRequest(`/unmatched-receipts/${encodeURIComponent(receiptId)}/resolve`, {
      method: "POST", body: JSON.stringify(payload)
    });
    closeResolveDialog();
    showMessage("receiptsMessage", "La API aceptó la solicitud. Actualiza la bandeja para comprobar el estado; la versión de prueba puede no guardar cambios.", "success");
    showToast("La API aceptó la solicitud; confirma en la bandeja si el cambio quedó persistido.", "success", "Solicitud enviada");
    await loadReceipts();
  } catch (error) {
    showMessage("resolveMessage", `No se pudo resolver la recepción: ${error.message}`, "error");
    showToast(`No se pudo resolver la recepción: ${error.message}`, "error", "No se completó la acción", 7500);
  } finally {
    if (submitButton) { submitButton.disabled = false; submitButton.textContent = "Confirmar relación"; }
  }
});
document.getElementById("refreshReceipts")?.addEventListener("click", loadReceipts);
document.addEventListener("DOMContentLoaded", loadReceipts);
