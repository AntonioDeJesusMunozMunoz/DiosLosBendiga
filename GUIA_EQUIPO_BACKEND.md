# 📘 Guía de Trabajo: Equipo Backend (3 Desarrolladores)
### Reto Hackathon: Almacén Inteligente para Taller

---

## 🎯 Objetivo General del Backend
Construir el servicio central de inventario en **Python (FastAPI + SQLite)** que escuche los eventos del taller desde Redpanda/Kafka, mantenga el saldo exacto en tiempo real, publique los eventos de inventario correspondientes y exponga una API HTTP en el puerto `8000`.

> **Puntos en juego para Backend:**
> - **25 Puntos:** Correctitud del Libro Mayor (Pruebas automáticas)
> - **25 Puntos:** Integración con el Taller (Pruebas automáticas)
> - **15 Puntos:** Documento de Decisiones de Arquitectura
> - **Total directo de Backend:** **65 de 100 puntos**

---

## 👥 División de Tareas por Desarrollador

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             BACKEND DEV 1                                   │
│  Libro Mayor, Base de Datos, Saldos Atómicos y Movimientos Físicos          │
├─────────────────────────────────────────────────────────────────────────────┤
│                             BACKEND DEV 2                                   │
│  Motor Kafka, Deduplicación, Órdenes, Inspecciones y Reservas               │
├─────────────────────────────────────────────────────────────────────────────┤
│                             BACKEND DEV 3                                   │
│  Compras, Algoritmo de Matching, Surtido de Faltantes y Reabasto            │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 🛠️ BACKEND DEV 1: Libro Mayor, Saldos y Movimientos Físicos

### 1. Responsabilidades Concretas
1. **Modelado del Estado y Base de Datos (SQLite con WAL mode):**
   - Tablas / entidades: `parts`, `locations`, `stock_balances` (por pieza y ubicación), `ledger_movements`, `reservations`, `shortages`, `processed_events`.
2. **Cálculo Atómico de Saldos:**
   $$\text{available} = \text{on\_hand} - \text{reserved}$$
   - Regla inviolable: $\text{available}$ **nunca puede ser menor a 0** en salidas o reservas.
3. **Endpoints a Implementar (`contract/openapi.yaml`):**
   - `GET /parts/{part_id}/availability`
   - `GET /parts/{part_id}/ledger`
   - `POST /issues`
   - `POST /transfers`
   - `POST /counts`

### 2. Necesidad que Cumple y Justificación

| Tarea / Endpoint | Regla del Contrato | Necesidad del Negocio / Criterio de Evaluación |
|---|:---:|---|
| `GET /parts/{id}/availability` | Regla 8 | **Cumple:** Visibilidad en tiempo real de saldos totales y desglosados por ubicación. Evalúa que $\text{available} = \text{on\_hand} - \text{reserved}$. |
| `GET /parts/{id}/ledger` | Regla 23 | **Cumple:** Trazabilidad auditora (Kardex). Debe responder en orden cronológico inverso (del más nuevo al más antiguo). Evalúa la prueba `kardex`. |
| `POST /issues` | Regla 16 | **Cumple:** Retiro físico de refacciones contra orden. Consume primero la reserva activa; si la entrega se completa, pasa a `fulfilled`. Si no hay saldo disponible en esa ubicación, **responde 409 Conflict** y no mueve nada. Emite `stock.issued`. |
| `POST /transfers` | Regla 11, 16 | **Cumple:** Mover refacciones entre estantes (ej. de recepción `U-100` a estante `U-101`). Si no alcanza el saldo origen, responde 409. Emite `stock.transferred`. |
| `POST /counts` | Regla 17 | **Cumple:** Conteo cíclico físico. Compara lo contado contra saldo del sistema y genera ajustes. **Obligatorio requerir `reason`** (si falta, responder 422). Es la única forma permitida donde `available` puede quedar negativo. Emite `stock.adjusted`. |
| Reconstrucción limpia | Regla 23 | **Cumple:** Permite que al borrar la base y releer los eventos de Kafka, el snapshot de saldos sea idéntico (Prueba `snapshot` / `compare` del jurado). |

---

## ⚙️ BACKEND DEV 2: Pipeline Kafka, Inspecciones y Reservas

### 1. Responsabilidades Concretas
1. **Consumidor y Productor Kafka (`confluent-kafka`):**
   - Conectarse al broker `localhost:19092` (o `redpanda:9092`).
   - Publicar salidas en `inventory.events` y eventos con fallo en `inventory.dlq`.
2. **Prioridad de Carga Inicial (¡CRÍTICO!):**
   - Consumir el tópico `shop.catalog` **completo hasta el final** antes de empezar a procesar `shop.work_orders`, `shop.inspections` y `shop.purchasing`.
3. **Idempotencia:**
   - Tabla `processed_events(event_id)`. Si un `event_id` ya fue procesado, descartar en silencio sin duplicar movimientos ni emitir nuevos eventos.
4. **Procesamiento de Inspecciones y Órdenes:**
   - `work_order.opened`, `work_order.stage_changed`, `work_order.deleted`.
   - `inspection.submitted`, `inspection.approved`, `inspection.rejected`, `inspection.discarded`, `inspection.voided`.
5. **Endpoints a Implementar:**
   - `GET /work-orders/{code}/materials`
   - `GET /shortages`

### 2. Necesidad que Cumple y Justificación

| Tarea / Evento | Regla del Contrato | Necesidad del Negocio / Criterio de Evaluación |
|---|:---:|---|
| Consumo secuencial de catálogo | Regla 22 | **Cumple:** Evita que compras o inspecciones fallen buscando piezas que aún no existen en el catálogo. Si esto no se hace, la prueba `compare` da 0. |
| `inspection.approved` | Reglas 1, 2, 5, 7 | **Cumple:** **El corazón del taller.** Solo las inspecciones aprobadas crean necesidad. Cada línea con `action = 'buy'` reserva $\min(\text{disponible}, \text{necesidad})$. Lo que no alcance emite `stock.shortage_detected`. Lo que alcance emite `stock.reserved`. |
| Reemplazo de necesidad | Regla 2, 3 | **Cumple:** Si una segunda aprobación de la misma orden trae una línea ya aprobada, **reemplaza** la cantidad (no se suma). Si sube, reserva la diferencia; si baja, libera el excedente. |
| Piezas del cliente | Regla 4 | **Cumple:** Renglones con `action = 'repair'` o `condition = 'ok'` **NO reservan nada** (es pieza del propio cliente). Clave para la prueba del jurado con el `GRIPPER`. |
| `inspection.voided` | Regla 13 | **Cumple:** Si se anula una inspección, se liberan las reservas asociadas y se cancelan sus faltantes. |
| `work_order.deleted` | Regla 14 | **Cumple:** Si una orden de trabajo se da de baja, se liberan todas sus reservas y se cierran sus faltantes. |
| `GET /work-orders/{code}/materials` | OpenAPI | **Cumple:** Expone el estado de materiales para que el frontend y el jurado vean qué está reservado, entregado o en faltante por orden. |
| `GET /shortages` | OpenAPI | **Cumple:** Filtra faltantes abiertos por orden de trabajo o por pieza. |

---

## 📦 BACKEND DEV 3: Compras, Matching Inteligente y Reabasto

### 1. Responsabilidades Concretas
1. **Consumo de `shop.purchasing` (`purchase.item_received`):**
   - Clasificar por `purpose`:
     - Si es `customer_order` $\rightarrow$ **Ignorar por completo** (material de reventa directa).
     - Si es `restock` $\rightarrow$ Ejecutar proceso de entrada al almacén.
2. **Algoritmo de Matching de Refacciones:**
   - Comparar `part_number` de la compra contra los `sku` del catálogo.
   - Normalización: `part_number.strip().upper()` vs `sku.strip().upper()`, ignorando caracteres especiales comunes.
   - Si no coincide con ninguna pieza, o coincide con más de una $\rightarrow$ Guardar como no relacionada y emitir `stock.unmatched_receipt`.
   - Si coincide unívocamente $\rightarrow$ Registrar entrada en la ubicación de recepción **`U-100` (`location_id: 100`)** y emitir `stock.received`.
3. **Surtido Automático de Faltantes:**
   - 1º Prioridad: Si la compra trae `work_order_code`, surtir primero los faltantes de esa orden específica.
   - 2º Prioridad: Con el remanente, surtir faltantes de otras órdenes del más antiguo al más reciente.
   - Por cada faltante surtido emitir: `stock.reserved` + `stock.shortage_resolved`.
4. **Sugerencias de Reorden:**
   - Si una pieza acumula faltantes abiertos o cae bajo stock de seguridad, calcular cantidad sugerida y emitir `stock.reorder_suggested`.
5. **Endpoints a Implementar:**
   - `GET /unmatched-receipts`
   - `POST /unmatched-receipts/{id}/resolve`
   - `GET /reorder-suggestions`

### 2. Necesidad que Cumple y Justificación

| Tarea / Evento | Regla del Contrato | Necesidad del Negocio / Criterio de Evaluación |
|---|:---:|---|
| Filtrado `customer_order` | Regla 9 | **Cumple:** Evita contaminar el stock del taller con compras destinadas a reventa al cliente. Pasa la prueba `compra_para_cliente`. |
| Normalización y Matching | Regla 10 | **Cumple:** Resuelve variaciones en números de proveedor (ej. `tornillo 6x12` vs `TORNILLO 6X12`). Si es ambiguo, protege la integridad emitiendo `stock.unmatched_receipt`. |
| Ubicación Recepción `U-100` | Regla 11 | **Cumple:** Todas las entradas físicas inician en la bahía de recepción `U-100`. Emite `stock.received`. |
| Surtido prioritario | Regla 12 | **Cumple:** Cumplimiento de entregas justo a tiempo. Resuelve la necesidad de la orden que motivó la compra antes que el inventario general. Pasa la prueba `reserva_faltante_compra_y_salida`. |
| `POST /unmatched-receipts/{id}/resolve` | OpenAPI | **Cumple:** Permite que un operador relacione a mano una compra pendiente. Al resolverla, entra a inventario y surte faltantes inmediatamente. |
| `GET /reorder-suggestions` | Regla 18 | **Cumple:** Alerta al departamento de compras sobre qué piezas se necesitan y qué órdenes las están esperando. |

---

## 📝 Documento de Decisiones Técnicas (15 Puntos del Jurado)
Los 3 desarrolladores de Backend redactarán juntos este documento (máximo 1 página) respondiendo con precisión quirúrgica:

1. **¿Cómo relacionan compras con el catálogo?**
   - *Respuesta:* Normalización canónica (conversión a mayúsculas, remoción de espacios al inicio/final y signos de puntuación redundantes). Detección de ambigüedad: si un `part_number` coincide con más de un SKU, se delega a revisión manual emitiendo `stock.unmatched_receipt`.
2. **¿Cómo manejan kits?**
   - *Respuesta:* Identificación por `group_name` en las líneas del BOM de la inspección, agrupando componentes bajo la misma necesidad de orden.
3. **¿Cómo evitan reservas dobles (Concurrencia)?**
   - *Respuesta:* Transacciones serializadas con locks a nivel de fila por `part_id` en la base de datos (o cola secuencial por llave de partición), impidiendo que dos aprobaciones simultáneas excedan el `available`.
4. **¿Cómo garantizan idempotencia?**
   - *Respuesta:* Registro transaccional del `event_id` en la tabla `processed_events`. Si un evento ya existe, se rechaza la ejecución antes de generar efectos secundarios o publicar en Kafka.
