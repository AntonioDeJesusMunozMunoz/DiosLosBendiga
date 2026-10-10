const orderParams = new URLSearchParams(window.location.search);
const initialOrderCode = orderParams.get("code");
if (initialOrderCode) document.getElementById("orderCode").value = initialOrderCode;

async function loadOrderShortages(code) {
  const body = document.getElementById("orderShortagesBody");
  const count = document.getElementById("shortageLineCount");
  body.innerHTML = `<tr><td colspan="3" class="empty">Consultando faltantes…</td></tr>`;
  try {
    const data = await apiRequest(`/shortages?work_order_code=${encodeURIComponent(code)}`);
    const items = listFromResponse(data);
    count.textContent = `${items.length} faltantes`;
    body.innerHTML = items.length ? items.map(item => `
      <tr>
        <td><span class="part-name">${escapeHTML(item.name || "Pieza sin nombre")}</span><span class="subtext">ID ${escapeHTML(item.part_id ?? "sin identificar")}</span></td>
        <td>${item.missing_quantity == null ? "—" : escapeHTML(item.missing_quantity)}</td>
        <td><span class="pill red">Abierto</span></td>
      </tr>`).join("") : `<tr><td colspan="3" class="empty">Esta orden no tiene faltantes abiertos.</td></tr>`;
  } catch (error) {
    count.textContent = "—";
    body.innerHTML = `<tr><td colspan="3" class="empty">No se pudieron consultar los faltantes: ${escapeHTML(error.message)}</td></tr>`;
  }
}

document.getElementById("orderForm")?.addEventListener("submit", async event => {
  event.preventDefault();
  const code = document.getElementById("orderCode").value.trim();
  const body = document.getElementById("materialsBody");
  body.innerHTML = `<tr><td colspan="6" class="empty">Consultando materiales…</td></tr>`;
  showMessage("orderMessage", "Consultando orden…");
  try {
    const data = await apiRequest(`/work-orders/${encodeURIComponent(code)}/materials`);
    const lines = Array.isArray(data.lines) ? data.lines : [];
    document.getElementById("orderTitle").textContent = `Orden ${data.code || code}`;
    document.getElementById("lineCount").textContent = `${lines.length} renglones`;
    body.innerHTML = lines.length ? lines.map(line => {
      const required = line.required;
      const reserved = Number(line.reserved ?? 0);
      const issued = Number(line.issued ?? 0);
      const missing = line.missing;
      const isCustomer = line.part_id === null;
      let status = `<span class="pill green">Reservado</span>`;
      if (isCustomer) status = `<span class="pill gray">Cliente / sin stock</span>`;
      else if (issued > 0 && required !== null && Number(required) <= issued) status = `<span class="pill blue">Entregado</span>`;
      else if (missing === null || Number(missing ?? 0) > 0) status = `<span class="pill red">Faltante</span>`;
      else if (reserved === 0 && issued === 0) status = `<span class="pill gray">Pendiente</span>`;
      else if (issued > 0) status = `<span class="pill blue">Parcialmente entregado</span>`;
      return `<tr>
        <td><span class="part-name">${escapeHTML(line.name)}</span><span class="subtext">Línea ${escapeHTML(line.bom_line_id)} · Pieza ${escapeHTML(line.part_id ?? "sin identificar")}</span></td>
        <td class="inline-number">${required === null ? "—" : escapeHTML(required)}</td>
        <td class="inline-number">${escapeHTML(reserved)}</td>
        <td class="inline-number">${escapeHTML(issued)}</td>
        <td class="inline-number">${missing === null ? "—" : escapeHTML(missing)}</td>
        <td>${status}</td>
      </tr>`;
    }).join("") : `<tr><td colspan="6" class="empty">La orden no tiene renglones de materiales vigentes.</td></tr>`;
    showMessage("orderMessage", `Consulta completada para ${data.code || code}.`, "success");
    showToast(`Se consultaron los materiales de la orden ${data.code || code}.`, "success", "Orden consultada");
    await loadOrderShortages(data.code || code);
  } catch (error) {
    document.getElementById("lineCount").textContent = "— renglones";
    body.innerHTML = `<tr><td colspan="6" class="empty">No fue posible consultar esta orden.</td></tr>`;
    showMessage("orderMessage", `Error al consultar la orden: ${error.message}`, "error");
    showToast(`No se pudo consultar la orden ${code}: ${error.message}`, "error", "Error de consulta", 7500);
    document.getElementById("shortageLineCount").textContent = "—";
    document.getElementById("orderShortagesBody").innerHTML = `<tr><td colspan="3" class="empty">No se pudo consultar esta orden.</td></tr>`;
  }
});

if (initialOrderCode) document.getElementById("orderForm")?.requestSubmit();

// Tabla general de órdenes. La lista inicial es de demostración porque la API
// actual no incluye un endpoint para enumerar todas las órdenes.
const demoOrders = [
  { code: "R061026001", description: "Orden de demostración 1" },
  { code: "R061026002", description: "Orden de demostración 2 · con faltantes" }
];

function renderOrdersTable(orders) {
  const body = document.getElementById("ordersOverviewBody");
  const count = document.getElementById("ordersOverviewCount");
  if (!body || !count) return;
  count.textContent = `${orders.length} ${orders.length === 1 ? "orden" : "órdenes"}`;
  body.innerHTML = orders.length ? orders.map(order => `
    <tr>
      <td><strong>${escapeHTML(order.code)}</strong></td>
      <td>${escapeHTML(order.description || "Orden de trabajo")}</td>
      <td><button class="button button-small" type="button" data-order-code="${escapeHTML(order.code)}">Consultar</button></td>
    </tr>`).join("") : `<tr><td colspan="3" class="empty">No hay órdenes que coincidan con la búsqueda.</td></tr>`;
  body.querySelectorAll("[data-order-code]").forEach(button => {
    button.addEventListener("click", () => {
      document.getElementById("orderCode").value = button.dataset.orderCode;
      document.getElementById("orderForm").requestSubmit();
    });
  });
}

function filterOrdersTable(code) {
  const query = code.trim().toLowerCase();
  const filtered = query ? demoOrders.filter(order => order.code.toLowerCase().includes(query) || order.description.toLowerCase().includes(query)) : demoOrders;
  renderOrdersTable(filtered);
  document.getElementById("ordersOverviewSection")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

renderOrdersTable(demoOrders);

document.getElementById("orderForm")?.addEventListener("submit", () => {
  filterOrdersTable(document.getElementById("orderCode").value);
});

document.getElementById("showAllOrders")?.addEventListener("click", () => {
  document.getElementById("orderCode").value = "";
  renderOrdersTable(demoOrders);
  document.getElementById("orderMessage").textContent = "Mostrando todas las órdenes de demostración.";
  document.getElementById("orderMessage").className = "message";
  document.getElementById("ordersOverviewSection")?.scrollIntoView({ behavior: "smooth", block: "start" });
});
