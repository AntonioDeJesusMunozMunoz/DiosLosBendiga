# 📱 Guía de Trabajo: Equipo Frontend (2 Desarrolladores)
### Reto Hackathon: Almacén Inteligente para Taller

---

## 🎯 Objetivo General del Frontend
Construir una interfaz web responsiva, ágil y moderna que cumpla dos propósitos:
1. **La aplicación móvil de piso (operada con cámara de celular):** Permitir a los técnicos retirar refacciones, realizar transferencias y registrar conteos físicos escaneando etiquetas **Code 128**.
2. **El panel web de almacén:** Ofrecer visibilidad total de órdenes de trabajo, existencias por ubicación, resolución manual de compras y trazabilidad del Kardex.

> **Puntos en juego para Frontend:**
> - **20 Puntos:** Experiencia en piso (Evaluada en vivo ante el jurado con celular)
> - **15 Puntos:** Extras / Valor agregado (Dashboard gerencial, rotación y alertas)
> - **Total directo de Frontend:** **35 de 100 puntos**

---

## 👥 División de Tareas por Desarrollador

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                             FRONTEND DEV 1                                  │
│  Móvil & Escáner en Piso (Celular / Cámara / Operador de Taller)            │
│  ¡LOS 20 PUNTOS DE LA DEMO ANTE EL JURADO!                                  │
├─────────────────────────────────────────────────────────────────────────────┤
│                             FRONTEND DEV 2                                  │
│  Panel Web de Almacén, Consulta de Órdenes, Kardex y Dashboard de Extras    │
└─────────────────────────────────────────────────────────────────────────────┘
```

---

## 📱 FRONTEND DEV 1: Experiencia en Piso y Escáner Móvil (20 Puntos)

### 1. El Libreto Secreto de la Demo del Jurado
*(Extraído de `CONTEXT.md`, sección 5.4)*:
El jurado calificará la experiencia en piso directamente en tu celular:
1. El jurado creará una orden de trabajo (Modelo **`GROB MSP-04`**, código `R...`).
2. Aprobará una inspección donde faltarán tornillos y arandelas.
3. Registrará una compra en el simulador que surtirá el faltante a la ubicación de recepción `U-100`.
4. **Aquí entras tú con el celular:**
   - Te pedirán: *"Ahora retira la pieza contra la orden"*.
   - Abres tu app móvil en el celular.
   - Escaneas con la cámara la etiqueta de ubicación **`U-100`**.
   - Escaneas la etiqueta de la pieza (ej. `TORNILLO 6X12`).
   - Retiras contra la orden `R...`.
   - **El saldo debe descontarse en tiempo real sin teclear nada.**

### 2. Responsabilidades Concretas
1. **Integración del Lector de Códigos de Barra:**
   - Usar la librería JavaScript **`html5-qrcode`** (o ZXing) para leer códigos tipo **Code 128** directamente desde el navegador móvil (Chrome / Safari).
   - Probar con cámara frontal y trasera (preferentemente trasera con autofocus).
2. **Pantalla 1: Salida de Refacciones (`POST /issues`):**
   - **Flujo en 3 toques:**
     - Campo 1: Código de Orden de Trabajo (escaneable o seleccionable de una lista).
     - Campo 2: Ubicación física (escaneando ej. `U-100` o `U-101`).
     - Campo 3: Refacción (escaneando el SKU, ej. `7014 CTA/P4` o `P-24`).
     - Selector de cantidad y botón grande *"Confirmar Salida"*.
   - **Manejo de Errores y Alertas:**
     - Si la API responde `409 Conflict`: Alerta en rojo *"Saldo insuficiente en esa ubicación. No se puede entregar"*.
     - Si la entrega es exitosa: Beep de confirmación y pantalla verde.
3. **Pantalla 2: Transferencias Rápidas (`POST /transfers`):**
   - Escanear ubicación origen (`from_location_id`, ej. `U-100`).
   - Escanear ubicación destino (`to_location_id`, ej. `U-105`).
   - Escanear pieza y cantidad $\rightarrow$ Botón *"Transferir"*.
4. **Pantalla 3: Conteo Cíclico en Estante (`POST /counts`):**
   - Escanear ubicación (ej. `U-102`).
   - Lista dinámica donde escaneas piezas y capturas lo contado físicamente.
   - Campo obligatorio: **Motivo del conteo** (ej. *"Auditoría semanal"* o *"Diferencia visual"*).
   - Botón *"Guardar Ajuste"*.

### 3. Necesidad que Cumple y Criterio de Evaluación

| Funcionalidad | Criterio de Evaluación | Justificación / Impacto en la Calificación |
|---|:---:|---|
| Escaneo con cámara sin teclear | 20 Pts (Demo) | **Pregunta del jurado:** *"¿Leyó las etiquetas impresas con la cámara del celular, sin teclear?"* Si el técnico tiene que escribir los códigos a mano, se pierden puntos. |
| Robustez de lectura Code 128 | Sección 4 del contrato | **Pregunta del jurado:** *"¿Aceptó `TOR, CAB, BAJA` y `7014 CTA/P4`?"* Las etiquetas reales traen comas, diagonales y espacios. El escáner no debe romper ni alterar estos caracteres. |
| Flujo rápido en pocos pasos | Ergonomía en piso | **Pregunta del jurado:** *"¿Cuántos toques para retirar o contar?"* La interfaz debe ser limpia, botones grandes tipo guante de taller y feedback sonoro/háptico. |

---

## 💻 FRONTEND DEV 2: Panel Web de Almacén, Órdenes y Extras

### 1. Responsabilidades Concretas
1. **Pantalla 1: Monitor de Órdenes de Trabajo (`GET /work-orders/{code}/materials`):**
   - Buscador por código de orden (ej. `R061026012`).
   - Vista tipo semáforo que desglosa cada renglón de la orden:
     - 🟢 **Verde:** Cantidad reservada en almacén (lista para retiro).
     - 🔴 **Rojo:** Faltante detectado (esperando compra).
     - 🔵 **Azul:** Entregada / cumplida.
     - ⚪ **Gris:** Pieza del cliente en reparación (no mueve stock).
2. **Pantalla 2: Existencias Generales y Kardex:**
   - Tabla con buscador por nombre o SKU (`GET /parts/{id}/availability`).
   - Al dar clic en una pieza, abrir el drawer/modal del **Kardex** (`GET /parts/{id}/ledger`): línea de tiempo con entradas, salidas, transferencias y saldos resultantes.
3. **Pantalla 3: Bandeja de Compras por Resolver (`GET /unmatched-receipts`):**
   - Cuando llega una compra con un nombre que el sistema no reconoció, aparece aquí.
   - Botón *"Resolver a mano"* con modal para buscar la pieza en el catálogo y llamar a `POST /unmatched-receipts/{id}/resolve`.
4. **Pantalla 4: Dashboard de Reabasto y Extras (Para los 15 Puntos Extra):**
   - Lista de sugerencias de compra vigentes (`GET /reorder-suggestions`).
   - Muestra qué pieza urge comprar, cuántas unidades y qué órdenes la están esperando.
   - Indicador visual de piezas críticas bajo stock mínimo.

### 2. Necesidad que Cumple y Criterio de Evaluación

| Funcionalidad | Criterio de Evaluación | Justificación / Impacto en la Calificación |
|---|:---:|---|
| Monitor visual por orden | Paso 3 y 5 de la Demo | **En la demo:** En cuanto el jurado apruebe la inspección, debes proyectar esta pantalla y mostrar: *"Mire, aquí está lo reservado en verde y lo que no alcanzó como faltante en rojo"*. Cuando llega la compra, se refresca y pasa a verde. |
| Bandeja de no relacionados | Regla 10 del Contrato | Permite subsanar compras ambiguas de proveedores sin detener la operación del taller. |
| Trazabilidad del Kardex | Sección 5 del Contrato | Permite auditar cualquier movimiento histórico ante reclamaciones del cliente o descuadres de inventario. |
| Dashboard de Reorden | 15 Pts (Extras) | Demuestra valor agregado gerencial: compras preventivas basadas en faltantes acumulados y modelos de husillos. |

---

## 🔗 Resumen de Endpoints del Backend que consume el Frontend

| Acción en Pantalla | Método y Ruta Backend | Quién lo consume |
|---|---|:---:|
| Salida de material | `POST /issues` | Frontend 1 (Móvil) |
| Mover entre estantes | `POST /transfers` | Frontend 1 (Móvil) |
| Ajuste por conteo | `POST /counts` | Frontend 1 (Móvil) |
| Consultar materiales de orden | `GET /work-orders/{code}/materials` | Frontend 2 (Web) |
| Consultar stock por estante | `GET /parts/{part_id}/availability` | Frontend 2 (Web) |
| Historial de movimientos | `GET /parts/{part_id}/ledger` | Frontend 2 (Web) |
| Compras pendientes | `GET /unmatched-receipts` | Frontend 2 (Web) |
| Asignar compra manual | `POST /unmatched-receipts/{id}/resolve` | Frontend 2 (Web) |
| Sugerencias de reorden | `GET /reorder-suggestions` | Frontend 2 (Web) |

---

## 💡 Recomendaciones Técnicas para Frontend
- **Servidor Único:** Colocar los archivos HTML/JS en la carpeta `static/` del backend FastAPI para que se sirvan en el mismo puerto `http://localhost:8000`.
- **Acceso desde Celular:** Durante el hackathon, para abrir la app en el celular sin cables, simplemente conecten el celular al mismo WiFi o hotspot de la laptop y abran `http://<IP_LOCAL_DE_LA_LAPTOP>:8000/scanner.html`.
