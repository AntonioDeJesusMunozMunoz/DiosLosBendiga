# ROCEEL — Frontend Dev 2

Esta carpeta contiene únicamente el trabajo de **Dev 2** según `GUIA_EQUIPO_FRONTEND.md`:
- Monitor de órdenes de trabajo.
- Existencias por pieza/ubicación y Kardex.
- Bandeja de compras sin relacionar.
- Dashboard de sugerencias de reabasto.

## Archivos
- `index.html`: dashboard de reabasto.
- `ordenes.html`: monitor de órdenes.
- `inventario.html`: disponibilidad por ubicación y Kardex en la misma pantalla.
- `faltantes.html`: compras por resolver y modal de relación manual.
- `css/styles.css`: estilos mobile-first, navegación plegable en móvil y distribución de escritorio desde 900 px.
- `js/app.js`: funciones compartidas para llamadas HTTP y formato. La URL de API usa el host actual en el puerto `8000`, con `localhost` como alternativa al abrir archivos directamente.
- `js/dashboard.js`, `js/ordenes.js`, `js/inventario.js`, `js/faltantes.js`: lógica de cada pantalla.
- `assets/roceel-logo.jpg`: logo recibido.

## Contrato API utilizado
Las rutas y campos se basan en `openapi.yaml`:
- `GET /reorder-suggestions` → `{ "items": [...] }`, cada sugerencia incluye `part_id`, `sku`, `name`, `suggested_quantity`, `work_order_ids`.
- `GET /unmatched-receipts` → `{ "items": [...] }`.
- `POST /unmatched-receipts/{id}/resolve` → cuerpo `{ "part_id": int, "location_id": int? }`.
- `GET /shortages` → `{ "items": [...] }`; el dashboard muestra los faltantes abiertos.
- `GET /shortages?work_order_code=...` → faltantes de una orden; la pantalla de órdenes muestra el resultado filtrado.
- `GET /work-orders/{code}/materials` → `work_order_id`, `code`, `lines[]` con `name`, `part_id`, `required`, `reserved`, `issued`, `missing`.
- `GET /parts/{part_id}/availability` → `part_id`, `on_hand`, `reserved`, `available`, `locations[]`.
- `GET /parts/{part_id}/ledger` → `part_id`, `entries[]` con `movement_id`, `type`, `quantity`, `occurred_at`, `reference`, `balance`.

**Importante:** el OpenAPI proporcionado no define un endpoint de búsqueda/listado de catálogo. Por eso la resolución manual pide el ID numérico de la pieza y avisa que se debe confirmar en el catálogo; no se inventó una ruta para buscar piezas.

## Integración con FastAPI
1. En la carpeta del backend, inicia la API con `py -3 -m uvicorn main:app --host 0.0.0.0 --port 8000`.
2. Comprueba la API en `http://localhost:8000/docs`.
3. Sirve el frontend desde un servidor estático accesible por la computadora y el móvil. La UI llama al host desde el que se abrió la página, usando el puerto `8000` para la API. En móvil, abre el frontend usando la IP local de la computadora, no `localhost`.
4. Si frontend y API usan puertos/orígenes distintos, el backend debe permitir ese origen mediante CORS.

No basta con abrir `/docs` para ver el frontend: `/docs` es la página de pruebas de FastAPI.

No se incluye la app móvil/escáner, salidas, transferencias ni conteos: eso corresponde a **Dev 1**.

## Ajustes incluidos en esta versión
- Las pantallas consumen los nombres de rutas y campos del contrato `openapi.yaml` compartido.
- Se manejan respuestas de error `{"detail": "..."}` y estados HTTP no exitosos desde el cliente común `apiRequest`.
- La pantalla de compras ya no afirma que el inventario se actualizó solo porque el endpoint de prueba devolvió éxito; la versión falsa puede no persistir cambios.
- No se agregaron rutas de búsqueda inventadas: la API compartida no define listado/búsqueda de órdenes ni catálogo de piezas.

## Cambios que debe coordinar el equipo backend
**No son bloqueantes para las consultas básicas actuales**, pero sí son necesarios si se quiere implementar autocompletado real por catálogo y órdenes:
1. Añadir `GET /parts?search=...` (o una ruta acordada equivalente) que busque por nombre, SKU e ID y devuelva una lista `{"items": [...]}` con al menos `part_id`, `sku` y `name`. Esto también permitiría seleccionar la pieza al resolver una recepción sin teclear su ID a ciegas.
2. Añadir `GET /work-orders?search=...` (o equivalente) que devuelva `{"items": [...]}` con `code` y, opcionalmente, etapa/estado para sugerir órdenes mientras se escribe.
3. Asegurar que `POST /unmatched-receipts/{id}/resolve` persista la resolución, registre la entrada y actualice los faltantes/saldos conforme al contrato. El `main.py` de prueba no demuestra persistencia.
4. Mantener la forma de respuesta definida en `openapi.yaml`; si el backend real cambia nombres/campos, debe avisarse y actualizarse el frontend en conjunto.

## Verificación rápida
1. Inicia FastAPI y abre la ruta del dashboard.
2. Revisa que las respuestas tengan el formato descrito arriba.
3. Prueba una orden real con su código.
4. Consulta una pieza por ID y revisa su saldo/Kardex.
5. Revisa los faltantes abiertos en el dashboard y por código de orden.
6. Resuelve una recepción de prueba usando un `part_id` válido.


## Diseño visual y adaptación móvil
La interfaz sigue un enfoque mobile-first: navegación plegable en pantallas pequeñas, formularios apilados en móvil, tablas desplazables horizontalmente y navegación lateral a partir de 900 px. Se mantiene el fondo #F8FAFC, texto #0F172A, bordes finos y verde #045127 para acciones principales y navegación activa. La pantalla de órdenes mantiene solo el título principal, el formulario y los resultados.


## Autocompletado de búsquedas

Los buscadores de Órdenes e Inventario incluyen sugerencias mediante `datalist` del navegador. Las opciones corresponden a los datos de demostración conocidos (`R061026001`, `R061026002` y las piezas 1001–1004); puedes seleccionar una sugerencia o escribir un código/ID manualmente. En Inventario se acepta el ID al inicio del valor sugerido.

**Limitación:** estas sugerencias son una lista de demostración local, no una búsqueda dinámica de todo el catálogo. El contrato de la API actual no incluye endpoints de búsqueda/listado de órdenes o piezas. Para sugerencias dinámicas reales, el backend debe acordar e implementar endpoints de búsqueda (por ejemplo `GET /work-orders?search=...` y `GET /parts?search=...`) o proporcionar una fuente equivalente de catálogo.


## Extras del dashboard y notificaciones
- El dashboard incluye un espacio de **Señales y recomendaciones** que prioriza faltantes abiertos, recepciones sin relacionar y sugerencias de reabasto usando los datos que devuelve la API.
- Se agregaron notificaciones flotantes compartidas para alertas operativas y errores de conexión/consulta; pueden cerrarse manualmente y desaparecen automáticamente.
- La prioridad actual es heurística: no es un modelo de IA ni un detector de patrones históricos. Para detectar tendencias de consumo, estacionalidad o piezas recurrentemente problemáticas, el backend deberá exponer historial de movimientos/consumo y acordar reglas o un modelo analítico.
- La alerta de stock mínimo solo puede determinarse de forma fiable si la API entrega existencias y el umbral mínimo de la pieza. El contrato actual de sugerencias no garantiza esos campos; no se debe presentar una inferencia como dato oficial si no vienen del backend.
