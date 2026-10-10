const API_HOST = window.location.hostname || "localhost"; const API_URL = "http://" + API_HOST + ":8000";
let transferData = {
    part_id: null,
    from_location_id: null,
    to_location_id: null,
    quantity: 1
};

let currentStep = 'from';
const valFrom = document.getElementById('val-from');
const valTo = document.getElementById('val-to');
const valPart = document.getElementById('val-part');
const inputQty = document.getElementById('input-qty');
const alertBox = document.getElementById('alert-box');
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

function setStep(step) {
    currentStep = step;
    document.querySelectorAll('.scan-card').forEach(c => c.classList.remove('active-target'));
    document.querySelector(`[data-field="${step}"]`).classList.add('active-target');
}

function validateAndProcessCode(code) {
    code = code.trim();

    if (currentStep === 'from' || currentStep === 'to') {
        if (!code.startsWith('U-')) {
            showAlert(`❌ Código inválido: "${code}" no es una Ubicación (Debe iniciar con U-)`, "error");
            return;
        }
        let cleanLoc = parseInt(code.replace('U-', '').trim()) || 100;
        
        if (currentStep === 'from') {
            transferData.from_location_id = cleanLoc;
            valFrom.textContent = code;
            showAlert(`✅ Origen capturado: ${code}`, "success");
            setStep('to');
        } else {
            transferData.to_location_id = cleanLoc;
            valTo.textContent = code;
            showAlert(`✅ Destino capturado: ${code}`, "success");
            setStep('part');
        }
    } else if (currentStep === 'part') {
        let cleanPart = parseInt(code.replace('P-', '').trim()) || 1001;
        transferData.part_id = cleanPart;
        valPart.textContent = code;
        showAlert(`✅ Pieza capturada: ${code}. ¡Listo!`, "success");
    }
}

confirmBtn.addEventListener('click', async () => {
    transferData.quantity = parseInt(inputQty.value) || 1;
    showAlert("Procesando transferencia...", "info");

    try {
        const response = await fetch(`${API_URL}/transfers`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(transferData)
        });
        const result = await response.json();
        if (response.ok) {
            showAlert("✅ Transferencia realizada con éxito", "success");
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