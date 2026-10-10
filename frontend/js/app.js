// Usa FastAPI en el mismo equipo. En móvil, la página debe abrirse usando
// la IP de la computadora; así se reutiliza ese host y el puerto 8000.
const API_HOST = window.location.hostname || "localhost";
const API_PROTOCOL = window.location.protocol === "https:" ? "https:" : "http:";
const API_BASE = `${API_PROTOCOL}//${API_HOST}:8000`;

async function apiRequest(path, options = {}) {
  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers: { ...(options.headers || {}), ...(options.body ? { "Content-Type": "application/json" } : {}) }
  });
  const raw = await response.text();
  let data = null;
  try { data = raw ? JSON.parse(raw) : null; } catch { data = raw; }
  if (!response.ok) {
    const detail = data && typeof data === "object" ? data.detail : null;
    throw new Error(detail || `Error HTTP ${response.status}`);
  }
  return data;
}

function showMessage(id, message, type = "") {
  const element = document.getElementById(id);
  if (!element) return;
  element.textContent = message;
  element.className = `message${type ? " " + type : ""}`;
}
function escapeHTML(value) {
  return String(value ?? "").replace(/[&<>"']/g, ch => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;"
  })[ch]);
}
function listFromResponse(data) {
  return Array.isArray(data?.items) ? data.items : Array.isArray(data) ? data : [];
}
function formatDate(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString("es-MX", { dateStyle: "short", timeStyle: "short" });
}
function movementLabel(type) {
  return ({ receipt: "Entrada", issue: "Salida", transfer_out: "Transferencia (salida)", transfer_in: "Transferencia (entrada)", adjustment: "Ajuste" })[type] || type || "Movimiento";
}
document.addEventListener("DOMContentLoaded", () => {
  const toggle = document.getElementById("menuToggle");
  const nav = document.getElementById("mainNav");
  if (toggle && nav) {
    toggle.addEventListener("click", () => {
      const isOpen = nav.classList.toggle("open");
      toggle.setAttribute("aria-expanded", String(isOpen));
    });
  }
});


// Notificaciones flotantes compartidas por todas las pantallas.
function showToast(message, type = "info", title = "ROCEEL", duration = 5200) {
  let region = document.getElementById("toastRegion");
  if (!region) {
    region = document.createElement("div");
    region.id = "toastRegion";
    region.className = "toast-region";
    region.setAttribute("role", "region");
    region.setAttribute("aria-label", "Notificaciones");
    region.setAttribute("aria-live", type === "error" || type === "warning" ? "assertive" : "polite");
    document.body.appendChild(region);
  }
  const toast = document.createElement("article");
  toast.className = `toast toast-${["success", "warning", "error"].includes(type) ? type : "info"}`;
  const symbol = type === "success" ? "✓" : type === "warning" ? "!" : type === "error" ? "×" : "i";
  const icon = document.createElement("span");
  icon.className = "toast-symbol";
  icon.setAttribute("aria-hidden", "true");
  icon.textContent = symbol;
  const content = document.createElement("div");
  const heading = document.createElement("strong");
  heading.textContent = title;
  const detail = document.createElement("p");
  detail.textContent = String(message ?? "");
  content.append(heading, detail);
  const close = document.createElement("button");
  close.className = "toast-close";
  close.type = "button";
  close.setAttribute("aria-label", "Cerrar notificación");
  close.textContent = "×";
  let removalTimer;
  let removalStarted = false;
  const dismiss = () => {
    if (removalStarted || !toast.isConnected) return;
    removalStarted = true;
    window.clearTimeout(removalTimer);
    toast.classList.add("is-leaving");
    toast.addEventListener("animationend", (event) => {
      if (event.animationName === "toast-out") toast.remove();
    }, { once: true });
    // Fallback por si animationend no se dispara en algún navegador.
    window.setTimeout(() => toast.remove(), 320);
  };
  close.addEventListener("click", dismiss);
  toast.append(icon, content, close);
  region.appendChild(toast);
  while (region.children.length > 4) region.firstElementChild && region.firstElementChild.remove();
  if (duration > 0) removalTimer = window.setTimeout(dismiss, duration);
  return toast;
}
