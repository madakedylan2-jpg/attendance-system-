/*
 * scanner.js
 * ---------------------------------------------------------------------
 * Software-only replacement for the physical barcode-scanner device
 * described in the original document (illuminator + sensor + decoder).
 *
 * Here, the same job — reading a Code128 barcode and extracting the
 * student's ID — is done entirely by:
 *   - the device's existing camera (laptop webcam / phone camera), read
 *     through the browser via the ZXing JS library, OR
 *   - manual keyboard entry, as an always-available fallback.
 *
 * Both paths call the same /api/attendance/scan endpoint, matching the
 * "Algorithm to Compliment the Proposed System" from the document:
 *   extract -> decode -> validate -> check duplicate -> record -> feedback
 * ---------------------------------------------------------------------
 */

function initAttendanceScanner(sessionId) {
  const statusEl = document.getElementById("scan-status");
  const feedEl = document.getElementById("scan-feed");
  const manualInput = document.getElementById("manual-barcode");
  const manualForm = document.getElementById("manual-scan-form");
  const startBtn = document.getElementById("start-camera-btn");
  const stopBtn = document.getElementById("stop-camera-btn");
  const readerEl = document.getElementById("reader");

  let codeReader = null;
  let scanning = false;
  let lastValue = null;
  let lastAt = 0;

  // Decode hints shared by BOTH the live-camera reader and the
  // upload-a-photo reader. Without TRY_HARDER, @zxing/library's default
  // decode pass is tuned for speed over thoroughness — fine for live
  // video (it just gets another frame a moment later if one fails), but
  // a single still image only gets one shot, so 1D barcodes (Code128
  // here) can silently fail to decode even on a clean, readable image.
  // Restricting POSSIBLE_FORMATS to CODE_128 also avoids wasted passes
  // trying to match QR/DataMatrix/etc. against the same pixels.
  function buildHints() {
    const hints = new Map();
    hints.set(ZXing.DecodeHintType.TRY_HARDER, true);
    hints.set(ZXing.DecodeHintType.POSSIBLE_FORMATS, [ZXing.BarcodeFormat.CODE_128]);
    return hints;
  }

  const confirmCard = document.getElementById("confirm-card");
  const confirmName = document.getElementById("confirm-name");
  const confirmReg = document.getElementById("confirm-reg");
  const confirmBadge = document.getElementById("confirm-badge");
  let confirmTimer = null;

  function showConfirmCard(data, ok) {
    if (!confirmCard) return;
    confirmCard.classList.remove("hidden", "err");
    if (!ok) confirmCard.classList.add("err");
    confirmName.textContent = data.student ? data.student.full_name : (ok ? "Marked" : "Not recognised");
    confirmReg.textContent = data.student ? data.student.reg_number : "";
    confirmBadge.innerHTML = data.status
      ? `<span class="badge ${data.status}">${data.status}</span>`
      : `<span class="badge absent">error</span>`;
    clearTimeout(confirmTimer);
    confirmTimer = setTimeout(() => confirmCard.classList.add("hidden"), 4000);
  }

  function setStatus(kind, message) {
    statusEl.className = "scan-status " + kind;
    statusEl.textContent = message;
  }

  function prependFeed(text, ok) {
    const row = document.createElement("div");
    row.className = "row";
    const time = new Date().toLocaleTimeString();
    row.innerHTML = `<span>${text}</span><span class="mono" style="color:${ok ? '#7CE0B4' : '#F3A29A'}">${time}</span>`;
    feedEl.prepend(row);
  }

  async function submitScan(barcodeValue, method) {
    // Debounce identical repeated camera reads within 3 seconds.
    const now = Date.now();
    if (method === "camera_scan" && barcodeValue === lastValue && now - lastAt < 3000) {
      return;
    }
    lastValue = barcodeValue;
    lastAt = now;

    try {
      const resp = await fetch("/api/attendance/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ barcode_value: barcodeValue, session_id: sessionId, method: method }),
      });
      const data = await resp.json();
      if (data.ok) {
        setStatus("ok", data.message);
        prependFeed(data.message, true);
        showConfirmCard(data, true);
      } else {
        setStatus("err", data.message);
        prependFeed(data.message, false);
        showConfirmCard(data, false);
      }
    } catch (err) {
      setStatus("err", "Network error while recording attendance.");
    }
  }

  // --- Manual entry (always available, no camera/hardware needed) -------
  if (manualForm) {
    manualForm.addEventListener("submit", (e) => {
      e.preventDefault();
      const val = manualInput.value.trim();
      if (!val) return;
      submitScan(val, "manual_entry");
      manualInput.value = "";
      manualInput.focus();
    });
  }

  // --- Camera-based scan (software replacement for the scanner device) --
  async function startCamera() {
    if (typeof ZXing === "undefined") {
      setStatus("err", "Camera library failed to load — use manual entry below.");
      return;
    }
    try {
      codeReader = new ZXing.BrowserMultiFormatReader(buildHints());
      const devices = await codeReader.listVideoInputDevices();
      if (!devices.length) {
        setStatus("err", "No camera detected on this device — use manual entry below.");
        return;
      }
      const deviceId = devices[0].deviceId;
      scanning = true;
      startBtn.style.display = "none";
      stopBtn.style.display = "inline-flex";
      setStatus("idle", "Camera active — point a student ID barcode at the camera.");
      codeReader.decodeFromVideoDevice(deviceId, readerEl, (result, err) => {
        if (result && scanning) {
          submitScan(result.getText(), "camera_scan");
        }
      });
    } catch (e) {
      const reason = (e && (e.name || e.message)) ? `${e.name || ""} ${e.message || ""}`.trim() : String(e);
      setStatus("err", `Camera error (${reason}) — use manual entry below.`);
      console.error("Camera start failed:", e);
    }
  }

  function stopCamera() {
    scanning = false;
    if (codeReader) {
      codeReader.reset();
    }
    startBtn.style.display = "inline-flex";
    stopBtn.style.display = "none";
    setStatus("idle", "Camera stopped.");
  }

  if (startBtn) startBtn.addEventListener("click", startCamera);
  if (stopBtn) stopBtn.addEventListener("click", stopCamera);

  // --- Upload-a-photo scan (decodes ONE still image, no live video) -----
  // Decoded SERVER-SIDE (via pyzbar) rather than in the browser. The
  // in-browser ZXing still-image decoder (decodeFromImage) turned out to
  // be unreliable on real photos/screenshots even with TRY_HARDER hints
  // set — it only gets one pass at a static image, unlike live camera
  // scanning which gets another frame a moment later if one fails.
  // pyzbar is a much more robust decoder and this sidesteps the
  // in-browser limitation entirely.
  const uploadBtn = document.getElementById("upload-photo-btn");
  const uploadInput = document.getElementById("upload-photo-input");

  if (uploadBtn && uploadInput) {
    uploadBtn.addEventListener("click", () => uploadInput.click());
    uploadInput.addEventListener("change", async () => {
      const file = uploadInput.files && uploadInput.files[0];
      if (!file) return;
      setStatus("idle", "Reading photo…");
      try {
        const formData = new FormData();
        formData.append("photo", file);
        formData.append("session_id", sessionId);

        const resp = await fetch("/api/attendance/scan_photo", {
          method: "POST",
          body: formData,
        });
        const data = await resp.json();
        if (data.ok) {
          setStatus("ok", data.message);
          prependFeed(data.message, true);
          showConfirmCard(data, true);
        } else {
          setStatus("err", data.message);
          prependFeed(data.message, false);
          showConfirmCard(data, false);
        }
      } catch (e) {
        setStatus("err", "Network error while reading that photo — try again, or use manual entry below.");
        console.error("Photo upload/decode failed:", e);
      } finally {
        uploadInput.value = ""; // allow re-selecting the same file
      }
    });
  }

  // Focus the manual field by default so a barcode "typed" by a USB
  // scan-emulator or a person can be captured without touching the mouse.
  if (manualInput) manualInput.focus();
}
