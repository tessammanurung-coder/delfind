"""
uploader.py — Smartbox Lost & Found
Mengirim data barang temuan (gambar + metadata) ke backend via HTTP multipart POST.
"""

from dotenv import load_dotenv
load_dotenv()

import logging
import os
from pathlib import Path

import httpx

log = logging.getLogger("Smartbox.Uploader")

BACKEND_URL = os.environ.get("SMARTBOX_BACKEND_URL", "http://localhost:8000")
API_SECRET  = os.environ.get("SMARTBOX_API_SECRET", "rahasia123")


def upload_found_item(image_path: Path, item_name: str, item_desc: str) -> bool:
    """
    Upload barang temuan ke backend.

    Args:
        image_path : Path ke file gambar
        item_name  : Nama barang hasil identifikasi AI
        item_desc  : Deskripsi barang

    Returns:
        True jika berhasil, False jika gagal
    """
    endpoint = f"{BACKEND_URL}/api/items/found"

    try:
        with open(image_path, "rb") as img_file:
            files   = {"image": (image_path.name, img_file, "image/jpeg")}
            data    = {
                "item_name"  : item_name,
                "description": item_desc,
                "location"   : "Smartbox Main Hall",  # Bisa diubah sesuai lokasi kotak
            }
            headers = {"X-API-Secret": API_SECRET}

            response = httpx.post(
                endpoint,
                files=files,
                data=data,
                headers=headers,
                timeout=30.0
            )

        if response.status_code == 201:
            log.info(f"Upload sukses. Item ID: {response.json().get('id')}")
            return True
        else:
            log.error(f"Upload gagal. Status: {response.status_code}, Body: {response.text[:300]}")
            return False

    except httpx.ConnectError:
        log.error(f"Tidak dapat terhubung ke backend di {endpoint}. Pastikan server berjalan.")
        return False
    except Exception as e:
        log.error(f"Error saat upload: {e}")
        return False