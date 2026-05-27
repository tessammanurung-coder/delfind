"""
detector.py — Smartbox Lost & Found
Mendeteksi barang baru di dalam kotak via webcam menggunakan OpenCV.
Algoritma: Background Subtraction (MOG2) + Stability Check → Capture → Kirim ke API
"""

from dotenv import load_dotenv
load_dotenv()

import cv2
import time
import logging
from pathlib import Path
from ai_identifier import identify_item
from uploader import upload_found_item

# ─── Konfigurasi ─────────────────────────────────────────────────────────────
CAMERA_INDEX        = 1         # Index webcam (0 = default)
MOTION_THRESHOLD    = 3000      # Luas piksel minimum yang bergerak (px²)
STABILITY_DURATION  = 3.0       # Detik barang harus diam sebelum capture
STABILITY_THRESHOLD = 500       # Max piksel berubah agar dianggap "diam"
COOLDOWN_AFTER_SEND = 10.0      # Detik jeda setelah satu barang berhasil dikirim
FRAME_WIDTH         = 1280
FRAME_HEIGHT        = 720
CAPTURE_DIR         = Path("captures")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("Smartbox")

# ─── State Machine ────────────────────────────────────────────────────────────
# Sistem punya 3 state:
#   IDLE      → tidak ada gerakan, menunggu
#   MOTION    → gerakan terdeteksi, menunggu barang diam
#   COOLDOWN  → barang sudah dikirim, jeda sebelum deteksi ulang

STATE_IDLE     = "IDLE"
STATE_MOTION   = "MOTION"
STATE_COOLDOWN = "COOLDOWN"


def preprocess_frame(frame):
    """Konversi frame ke grayscale + blur untuk mengurangi noise."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(gray, (21, 21), 0)


def compute_motion_area(fgmask):
    """Hitung luas area bergerak dari foreground mask MOG2."""
    # Morphological ops untuk menghilangkan noise kecil
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
    cleaned = cv2.morphologyEx(fgmask, cv2.MORPH_OPEN, kernel)
    cleaned = cv2.morphologyEx(cleaned, cv2.MORPH_CLOSE, kernel)
    return cv2.countNonZero(cleaned)


def compute_frame_diff(prev_gray, curr_gray):
    """Hitung jumlah piksel berbeda antara dua frame (untuk stability check)."""
    diff = cv2.absdiff(prev_gray, curr_gray)
    _, thresh = cv2.threshold(diff, 25, 255, cv2.THRESH_BINARY)
    return cv2.countNonZero(thresh)


def save_capture(frame, timestamp):
    """Simpan frame sebagai file JPEG."""
    CAPTURE_DIR.mkdir(exist_ok=True)
    filename = CAPTURE_DIR / f"capture_{int(timestamp)}.jpg"
    # Resize ke 640x480 untuk menghemat bandwidth saat upload
    resized = cv2.resize(frame, (640, 480))
    cv2.imwrite(str(filename), resized, [cv2.IMWRITE_JPEG_QUALITY, 85])
    log.info(f"Gambar disimpan: {filename}")
    return filename


def run_detector():
    cap = cv2.VideoCapture(CAMERA_INDEX)
    if not cap.isOpened():
        log.error("Gagal membuka kamera! Periksa index kamera.")
        return

    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)

    # Inisialisasi background subtractor MOG2
    # history=500: panjang riwayat frame untuk model background
    # varThreshold=50: sensitivitas deteksi (lebih kecil = lebih sensitif)
    # detectShadows=False: nonaktifkan deteksi bayangan untuk performa lebih cepat
    bg_subtractor = cv2.createBackgroundSubtractorMOG2(
        history=500, varThreshold=50, detectShadows=False
    )

    state           = STATE_IDLE
    stable_since    = None      # Waktu mulai barang diam
    cooldown_until  = 0         # Timestamp akhir cooldown
    prev_gray       = None      # Frame sebelumnya (untuk stability check)
    captured_frame  = None      # Frame terbaik untuk dikirim

    log.info("Smartbox aktif. Menunggu barang...")

    while True:
        ret, frame = cap.read()
        if not ret:
            log.warning("Frame tidak terbaca, mencoba ulang...")
            time.sleep(0.1)
            continue

        now       = time.time()
        curr_gray = preprocess_frame(frame)
        fgmask    = bg_subtractor.apply(frame)
        motion_area = compute_motion_area(fgmask)

        # ── STATE: COOLDOWN ───────────────────────────────────────────────────
        if state == STATE_COOLDOWN:
            if now >= cooldown_until:
                log.info("Cooldown selesai. Kembali ke mode IDLE.")
                state = STATE_IDLE
            prev_gray = curr_gray
            # Tampilkan preview (opsional, hapus di production headless)
            _show_debug(frame, fgmask, state, motion_area)
            continue

        # ── STATE: IDLE ───────────────────────────────────────────────────────
        if state == STATE_IDLE:
            if motion_area > MOTION_THRESHOLD:
                log.info(f"Gerakan terdeteksi! Area: {motion_area}px². Masuk mode MOTION.")
                state        = STATE_MOTION
                stable_since = None
            prev_gray = curr_gray
            _show_debug(frame, fgmask, state, motion_area)
            continue

        # ── STATE: MOTION ─────────────────────────────────────────────────────
        if state == STATE_MOTION:
            if prev_gray is not None:
                frame_diff = compute_frame_diff(prev_gray, curr_gray)

                if frame_diff < STABILITY_THRESHOLD:
                    # Frame mulai stabil
                    if stable_since is None:
                        stable_since = now
                        log.info("Barang mulai stabil, menghitung durasi...")

                    stable_duration = now - stable_since
                    log.debug(f"Stabil selama {stable_duration:.1f}s / {STABILITY_DURATION}s")

                    if stable_duration >= STABILITY_DURATION:
                        # ✅ TRIGGER CAPTURE
                        log.info(f"Barang stabil {STABILITY_DURATION}s! Melakukan capture...")
                        captured_frame = frame.copy()
                        filepath = save_capture(captured_frame, now)

                        # Identifikasi via AI
                        log.info("Mengirim ke Gemini Vision API...")
                        item_name, item_desc = identify_item(filepath)
                        log.info(f"AI mengidentifikasi: '{item_name}' — {item_desc}")

                        # Upload ke backend
                        success = upload_found_item(filepath, item_name, item_desc)
                        if success:
                            log.info("✅ Data berhasil dikirim ke backend!")
                        else:
                            log.error("❌ Gagal mengirim data ke backend.")

                        # Masuk cooldown
                        state         = STATE_COOLDOWN
                        cooldown_until = now + COOLDOWN_AFTER_SEND
                        stable_since  = None

                else:
                    # Masih bergerak, reset timer stabilitas
                    if stable_since is not None:
                        log.debug("Barang bergerak lagi, reset timer.")
                        stable_since = None

                    # Jika gerakan sudah terlalu kecil, kembali ke IDLE
                    if motion_area < MOTION_THRESHOLD // 2:
                        log.info("Gerakan hilang tanpa stabilitas. Kembali ke IDLE.")
                        state        = STATE_IDLE
                        stable_since = None

            prev_gray = curr_gray
            _show_debug(frame, fgmask, state, motion_area)

        # Keluar dengan 'q'
        if cv2.waitKey(1) & 0xFF == ord('q'):
            log.info("Keluar dari program.")
            break

    cap.release()
    cv2.destroyAllWindows()


def _show_debug(frame, fgmask, state, motion_area):
    """Tampilkan preview debug (nonaktifkan di production headless/server)."""
    display = frame.copy()
    color = {"IDLE": (0, 255, 0), "MOTION": (0, 165, 255), "COOLDOWN": (0, 0, 255)}
    cv2.putText(display, f"State: {state}", (10, 30),
                cv2.FONT_HERSHEY_SIMPLEX, 0.8, color.get(state, (255,255,255)), 2)
    cv2.putText(display, f"Motion: {motion_area}px2", (10, 60),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, (200, 200, 200), 1)
    cv2.imshow("Smartbox Preview", display)
    cv2.imshow("Foreground Mask", fgmask)


if __name__ == "__main__":
    run_detector()