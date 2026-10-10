const API_HOST = window.location.hostname || "localhost"; const API_URL = "http://" + API_HOST + ":8000";

let data = {
    work_order_code: "",
    part_id: null,
    location_id: null,
    quantity: 1,
    issued_by: "Operador-Taller"
};

let currentStep = 'order';
const valOrder = document.getElementById('val-order');
const valLocation = document.getElementById('val-location');
const valSku = document.getElementById('val-sku');
const alertBox = document.getElementById('alert-box');
const inputQty = document.getElementById('input-qty');
const confirmBtn = document.getElementById('confirmBtn');
const toggleScanBtn = document.getElementById('toggleScanBtn');

let html5QrCode = null;
let isScanning = false;
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

        // Temporizador de ayuda si tarda más de 7 segundos en leer
        scanTimeout = setTimeout(() => {
            if (isScanning) {
                showAlert("⚠️ ¿Problemas para leerlo? Intenta alejar/acercar el teléfono o verifica si la etiqueta está dañada.", "error");
            }
        }, 7000);

    }).catch(err => {
        showAlert("No se pudo acceder a la cámara.", "error");
    });
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

function setStep(step) {
    currentStep = step;
    document.querySelectorAll('.scan-card').forEach(card => card.classList.remove('active-target'));
    document.querySelector(`[data-field="${step}"]`).classList.add('active-target');
}

function validateAndProcessCode(code) {
    code = code.trim();

    if (currentStep === 'order') {
        if (!code.startsWith('R') && code.length < 5) {
            showAlert(`❌ Código inválido para Orden: "${code}". Debe iniciar con R...`, "error");
            return;
        }
        data.work_order_code = code;
        valOrder.textContent = code;
        showAlert(`✅ Orden capturada: ${code}`, "success");
        setStep('location');

    } else if (currentStep === 'location') {
        if (!code.startsWith('U-')) {
            showAlert(`❌ Código inválido: "${code}" no es una Ubicación (Debe iniciar con U-)`, "error");
            return;
        }
        let cleanLoc = code.replace('U-', '').trim();
        data.location_id = parseInt(cleanLoc) || 100; 
        valLocation.textContent = code;
        showAlert(`✅ Ubicación capturada: ${code}`, "success");
        setStep('sku');

    } else if (currentStep === 'sku') {
        let cleanPart = code.replace('P-', '').trim();
        data.part_id = parseInt(cleanPart) || 1001;
        valSku.textContent = code;
        showAlert(`✅ Pieza capturada: ${code}. ¡Listo para confirmar!`, "success");
    }
}

function modifyQty(delta) { data.quantity = Math.max(1, (data.quantity || 1) + delta); document.getElementById('qty-display').textContent = data.quantity; }

confirmBtn.addEventListener('click', async () => {
    showAlert("Enviando datos al servidor...", "info");
    try {
        const response = await fetch(`${API_URL}/issues`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        const result = await response.json();
        if (response.ok) {
            showAlert("✅ Salida registrada con éxito.", "success");
        } else {
            showAlert(`❌ Error (${response.status}): ${result.detail}`, "error");
        }
    } catch (error) {
        showAlert("❌ No se pudo conectar con la API.", "error");
    }
});

function showAlert(message, type) {
    alertBox.textContent = message;
    alertBox.className = type;
}