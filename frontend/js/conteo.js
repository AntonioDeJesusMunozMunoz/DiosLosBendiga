const API_HOST = window.location.hostname || "localhost"; const API_URL = "http://" + API_HOST + ":8000";

let countData = {
    location_id: null,
    reason: "",
    lines: [] 
};

const valLocation = document.getElementById('val-location');
const inputReason = document.getElementById('input-reason');
const linesContainer = document.getElementById('lines-container');
const alertBox = document.getElementById('alert-box');
const confirmBtn = document.getElementById('confirmBtn');
const toggleScanBtn = document.getElementById('toggleScanBtn');

let html5QrCode = null;
let isScanning = false;
let scanningStage = 'location';
let scanTimeout = null;

toggleScanBtn.addEventListener('click', () => {
    if (!isScanning) startScanner();
    else stopScanner();
});

function startScanner() {
    document.getElementById('reader-container').style.display = 'block';
    html5QrCode = new Html5Qrcode("reader");
    html5QrCode.start(
        { facingMode: "environment" },
        { 
            fps: 10, 
            qrbox: { width: 250, height: 150 },
            formatsToSupport: [ 
                Html5QrcodeSupportedFormats.CODE_128, 
                Html5QrcodeSupportedFormats.CODE_39 
            ]
        },
        (decodedText) => {
            clearTimeout(scanTimeout);
            stopScanner();
            validateAndProcessCode(decodedText);
        },
        (err) => {}
    ).then(() => {
        isScanning = true;
        toggleScanBtn.textContent = "🛑 Detener Cámara";
        toggleScanBtn.style.background = "#c0392b";
        showAlert("Buscando código... (Apunta bien y evita reflejos)", "info");

        scanTimeout = setTimeout(() => {
            if (isScanning) {
                showAlert("⚠️ ¿Problemas para leerlo? Intenta alejar/acercar el teléfono o verifica la etiqueta.", "error");
            }
        }, 7000);

    }).catch(() => showAlert("Error al abrir cámara", "error"));
}

function stopScanner() {
    clearTimeout(scanTimeout);
    if (html5QrCode && isScanning) {
        html5QrCode.stop().then(() => {
            isScanning = false;
            toggleScanBtn.textContent = "📷 Activar Escáner";
            toggleScanBtn.style.background = "#2980b9";
            document.getElementById('reader-container').style.display = 'none';
        }).catch(() => {
            isScanning = false;
            document.getElementById('reader-container').style.display = 'none';
        });
    }
}

function validateAndProcessCode(code) {
    code = code.trim();

    if (scanningStage === 'location') {
        if (!code.startsWith('U-')) {
            showAlert(`❌ Código inválido: "${code}" no es una Ubicación (Debe iniciar con U-)`, "error");
            return;
        }
        countData.location_id = parseInt(code.replace('U-', '').trim()) || 102;
        valLocation.textContent = code;
        scanningStage = 'parts';
        showAlert("✅ Ubicación fijada. Vuelve a activar la cámara para escanear piezas.", "success");
    } else {
        let partId = parseInt(code.replace('P-', '').trim()) || 1001;
        let qtyStr = prompt(`Cantidad física contada para la pieza ${code}:`, "10");
        let countedQty = parseInt(qtyStr);

        if (!isNaN(countedQty)) {
            countData.lines.push({ part_id: partId, counted_quantity: countedQty });
            renderLines();
            showAlert(`✅ Pieza ${code} agregada (${countedQty} un.)`, "success");
        }
    }
}

function renderLines() {
    if (countData.lines.length === 0) {
        linesContainer.innerHTML = "Sin piezas agregadas";
        return;
    }
    linesContainer.innerHTML = countData.lines.map(l => 
        `<div>• Pieza ID: ${l.part_id} ➔ <b>${l.counted_quantity} un.</b></div>`
    ).join('');
}

confirmBtn.addEventListener('click', async () => {
    countData.reason = inputReason.value.trim();
    if (!countData.reason) {
        showAlert("❌ El motivo del conteo es obligatorio", "error");
        return;
    }

    showAlert("Guardando ajuste de inventario...", "info");

    try {
        const response = await fetch(`${API_URL}/counts`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(countData)
        });
        const result = await response.json();
        if (response.ok) {
            showAlert("✅ Conteo guardado con éxito", "success");
        } else {
            showAlert(`❌ Error (${response.status}): ${result.detail}`, "error");
        }
    } catch (e) {
        showAlert("❌ Error de conexión con la API", "error");
    }
});

function showAlert(msg, type) {
    alertBox.textContent = msg;
    alertBox.className = type;
}