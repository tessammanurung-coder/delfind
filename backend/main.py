"""
main.py — Smartbox Lost & Found Backend
FastAPI server yang menerima data dari edge script dan melayani frontend.

Jalankan dengan: uvicorn main:app --reload --port 8000
"""

import os
import shutil
import uuid
from datetime import datetime
import pytz
from pathlib import Path
from typing import Optional, List

from fastapi import FastAPI, File, Form, UploadFile, HTTPException, Depends, Header
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from sqlalchemy import Column, Integer, String, DateTime, Text, create_engine, text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from pydantic import BaseModel


# ─── Konfigurasi ─────────────────────────────────────────────────────────────
DATABASE_URL  = os.environ.get("DATABASE_URL", "sqlite:///./smartbox.db")
UPLOAD_DIR    = Path("uploads")
API_SECRET    = os.environ.get("SMARTBOX_API_SECRET", "rahasia123") 
UPLOAD_DIR.mkdir(exist_ok=True)
JAKARTA_TZ = pytz.timezone("Asia/Jakarta")

# ─── Database Setup ───────────────────────────────────────────────────────────
engine       = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base         = declarative_base()


def get_local_now():
    """Fungsi helper untuk mengambil waktu lokal saat ini (WIB / Asia Jakarta)."""
    return datetime.now(JAKARTA_TZ)

class FoundItem(Base):
    """Barang yang ditemukan dan dilaporkan oleh Smartbox."""
    __tablename__ = "items_found"

    id          = Column(Integer, primary_key=True, index=True)
    item_name   = Column(String(200), nullable=False)
    description = Column(Text, default="")
    location    = Column(String(200), default="Smartbox")
    image_path  = Column(String(500), nullable=True)
    status      = Column(String(50), default="unclaimed")   # unclaimed / claimed
    created_at  = Column(DateTime, default=get_local_now)


class LostItem(Base):
    """Laporan barang hilang yang disubmit manual oleh user dengan dukungan foto."""
    __tablename__ = "items_lost"

    id              = Column(Integer, primary_key=True, index=True)
    item_name       = Column(String(200), nullable=False)
    description     = Column(Text, default="")
    lost_location   = Column(String(200), default="")
    contact_name    = Column(String(200), nullable=False)
    contact_phone   = Column(String(50), nullable=False)
    contact_email   = Column(String(200), default="")
    image_path      = Column(String(500), nullable=True)
    created_at      = Column(DateTime, default=get_local_now) # SINKRONISASI: Diubah dari utcnow ke get_local_now


# Terapkan skema dasar tabel
Base.metadata.create_all(bind=engine)


# ─── AUTO MIGRATION (Mencegah Crash Saat Tambah Kolom) ──────────────────────
def apply_auto_migration():
    """
    Memeriksa tabel items_lost secara otomatis di database fisik.
    Jika kolom image_path belum tersedia, kolom akan disuntikkan secara aman.
    """
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA table_info(items_lost);")).fetchall()
        columns = [row[1] for row in result]
        
        if "image_path" not in columns:
            try:
                conn.execute(text("ALTER TABLE items_lost ADD COLUMN image_path VARCHAR(500);"))
                conn.commit()
                print("======= AUTO MIGRATION: Kolom image_path berhasil ditambahkan ke tabel items_lost! =======")
            except Exception as e:
                print(f"Peringatan Auto Migration: {e}")

# Jalankan skrip migrasi kolom otomatis sebelum app menerima request
apply_auto_migration()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ─── Pydantic Schemas (Response) ─────────────────────────────────────────────
class FoundItemResponse(BaseModel):
    id          : int
    item_name   : str
    description : str
    location    : str
    image_url   : Optional[str]
    status      : str
    created_at  : datetime

    class Config:
        from_attributes = True


class LostItemResponse(BaseModel):
    id            : int
    item_name     : str
    description   : str
    lost_location : str
    contact_name  : str
    contact_phone : str
    contact_email : str
    image_url     : Optional[str]  
    created_at    : datetime

    class Config:
        from_attributes = True


# Schema untuk menerima request aksi massal (Bulk Actions)
class BulkActionRequest(BaseModel):
    ids: List[int]


# ─── FastAPI App ──────────────────────────────────────────────────────────────
app = FastAPI(
    title="Smartbox Lost & Found API",
    description="API untuk sistem Lost & Found berbasis Computer Vision",
    version="1.0.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve uploaded images sebagai static files
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")


# ─── Dependency: Autentikasi API Secret ──────────────────────────────────────
def verify_api_secret(x_api_secret: str = Header(None)):
    """Validasi secret key dari edge device. Hanya untuk endpoint POST dari Smartbox."""
    if x_api_secret != API_SECRET:
        raise HTTPException(status_code=401, detail="Invalid API secret")
    return True


# ─── Endpoints ───────────────────────────────────────────────────────────────

@app.get("/health")
def health_check():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


# ── Barang Temuan (from Smartbox edge device) ─────────────────────────────────

@app.post("/api/items/found", status_code=201)
async def create_found_item(
    item_name  : str        = Form(...),
    description: str        = Form(""),
    location   : str        = Form("Smartbox"),
    image      : UploadFile = File(...),
    db         : Session    = Depends(get_db),
    _          : bool       = Depends(verify_api_secret),
):
    file_ext      = Path(image.filename).suffix or ".jpg"
    unique_name   = f"{uuid.uuid4().hex}{file_ext}"
    image_dest    = UPLOAD_DIR / unique_name

    with open(image_dest, "wb") as buffer:
        shutil.copyfileobj(image.file, buffer)

    db_item = FoundItem(
        item_name   = item_name,
        description = description,
        location    = location,
        image_path  = str(unique_name),
        status      = "unclaimed",
        created_at  = get_local_now() # Memastikan waktu pengisian dari edge terekam dalam WIB
    )
    db.add(db_item)
    db.commit()
    db.refresh(db_item)

    return _serialize_found_item(db_item)


# ── Barang Temuan Input Manual dari Web ───────────────────────────────────────

@app.post("/api/items/found/manual", status_code=201)
async def create_found_item_manual(
    item_name  : str                    = Form(...),
    description: str                    = Form(""),
    location   : str                    = Form(...),
    image      : Optional[UploadFile]   = File(None),
    db         : Session                = Depends(get_db),
):
    unique_name = None
    if image and image.filename:
        file_ext    = Path(image.filename).suffix or ".jpg"
        unique_name = f"{uuid.uuid4().hex}{file_ext}"
        image_dest  = UPLOAD_DIR / unique_name

        with open(image_dest, "wb") as buffer:
            shutil.copyfileobj(image.file, buffer)

    db_item = FoundItem(
        item_name   = item_name,
        description = description,
        location    = location,
        image_path  = str(unique_name) if unique_name else None,
        status      = "unclaimed",
        created_at  = get_local_now() # Memastikan waktu manual terekam dalam WIB
    )
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    
    return _serialize_found_item(db_item)


@app.get("/api/items/found")
def list_found_items(
    skip  : int = 0,
    limit : int = 50,
    status: Optional[str] = None,
    db    : Session = Depends(get_db),
):
    query = db.query(FoundItem).order_by(FoundItem.created_at.desc())
    if status:
        query = query.filter(FoundItem.status == status)
    items = query.offset(skip).limit(limit).all()
    return [_serialize_found_item(i) for i in items]


@app.patch("/api/items/found/{item_id}/claim")
def claim_found_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(FoundItem).filter(FoundItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    item.status = "claimed"
    db.commit()
    return {"message": "Item marked as claimed", "id": item_id}


@app.delete("/api/items/found/{item_id}", status_code=200)
def delete_found_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(FoundItem).filter(FoundItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Item tidak ditemukan")
    
    if item.image_path:
        file_path = UPLOAD_DIR / item.image_path
        if file_path.exists():
            file_path.unlink()

    db.delete(item)
    db.commit()
    return {"message": f"Barang temuan dengan ID {item_id} berhasil dihapus"}


# ── Barang Hilang ─────────────────────────────────────────────────────────────

@app.post("/api/items/lost", status_code=201)
async def create_lost_item(
    item_name     : str                    = Form(...),
    description   : str                    = Form(""),
    lost_location : str                    = Form(""),
    contact_name  : str                    = Form(...),
    contact_phone : str                    = Form("0000"),  
    contact_email : str                    = Form(""),      
    image         : Optional[UploadFile]   = File(None),  
    db            : Session                = Depends(get_db),
):
    unique_name = None
    
    if image and image.filename:
        file_ext    = Path(image.filename).suffix or ".jpg"
        unique_name = f"{uuid.uuid4().hex}{file_ext}"
        image_dest  = UPLOAD_DIR / unique_name

        with open(image_dest, "wb") as buffer:
            shutil.copyfileobj(image.file, buffer)

    db_item = LostItem(
        item_name     = item_name,
        description   = description,
        lost_location = lost_location,
        contact_name  = contact_name,
        contact_phone = contact_phone,
        contact_email = contact_email,
        image_path    = str(unique_name) if unique_name else None,
        created_at    = get_local_now() # SINKRONISASI: Pengisian barang hilang direkam menggunakan WIB
    )
    db.add(db_item)
    db.commit()
    db.refresh(db_item)
    return _serialize_lost_item(db_item)


@app.get("/api/items/lost")
def list_lost_items(skip: int = 0, limit: int = 50, db: Session = Depends(get_db)):
    items = db.query(LostItem).order_by(LostItem.created_at.desc()).offset(skip).limit(limit).all()
    return [_serialize_lost_item(i) for i in items]


@app.delete("/api/items/lost/{item_id}", status_code=200)
def delete_lost_item(item_id: int, db: Session = Depends(get_db)):
    item = db.query(LostItem).filter(LostItem.id == item_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="Laporan kehilangan tidak ditemukan")
    
    if item.image_path:
        file_path = UPLOAD_DIR / item.image_path
        if file_path.exists():
            file_path.unlink()
            
    db.delete(item)
    db.commit()
    return {"message": f"Laporan kehilangan dengan ID {item_id} berhasil dihapus"}


# ─── FITUR INTERAKSI MASSAL (BULK ACTIONS) ────────────────────────────────────

@app.post("/api/items/found/bulk-claim")
def bulk_claim_found_items(payload: BulkActionRequest, db: Session = Depends(get_db)):
    if not payload.ids:
        raise HTTPException(status_code=400, detail="Daftar ID kosong")
        
    items = db.query(FoundItem).filter(FoundItem.id.in_(payload.ids)).all()
    for item in items:
        item.status = "claimed"
        
    db.commit()
    return {"status": "success", "message": f"{len(items)} barang berhasil diklaim massal"}

@app.post("/api/items/found/bulk-delete")
def bulk_delete_found_items(payload: BulkActionRequest, db: Session = Depends(get_db)):
    if not payload.ids:
        raise HTTPException(status_code=400, detail="Daftar ID kosong")
        
    items = db.query(FoundItem).filter(FoundItem.id.in_(payload.ids)).all()
    for item in items:
        if item.image_path:
            file_path = UPLOAD_DIR / item.image_path
            if file_path.exists():
                file_path.unlink()
        db.delete(item)
        
    db.commit()
    return {"status": "success", "message": "Log temuan terpilih berhasil dihapus massal"}

@app.post("/api/items/lost/bulk-delete")
def bulk_delete_lost_items(payload: BulkActionRequest, db: Session = Depends(get_db)):
    if not payload.ids:
        raise HTTPException(status_code=400, detail="Daftar ID kosong")
        
    items = db.query(LostItem).filter(LostItem.id.in_(payload.ids)).all()
    for item in items:
        if item.image_path:
            file_path = UPLOAD_DIR / item.image_path
            if file_path.exists():
                file_path.unlink()
        db.delete(item)
        
    db.commit()
    return {"status": "success", "message": "Log kehilangan terpilih berhasil dihapus massal"}


# ─── Helpers (Serialization Format Dinamis & ISO Standard) ────────────────────

def _serialize_found_item(item: FoundItem) -> dict:
    # Mengonversi format tanggal menjadi ISO format String yang seragam agar frontend tidak crash saat sorting
    iso_date = item.created_at.isoformat() if hasattr(item.created_at, 'isoformat') else str(item.created_at)
    return {
        "id"         : item.id,
        "item_name"  : item.item_name,
        "description": item.description if item.description else "",
        "location"   : item.location,
        "image_url"  : f"/uploads/{item.image_path}" if item.image_path else None,
        "status"     : item.status,
        "created_at" : iso_date,
    }

def _serialize_lost_item(item: LostItem) -> dict:
    iso_date = item.created_at.isoformat() if hasattr(item.created_at, 'isoformat') else str(item.created_at)
    return {
        "id"            : item.id,
        "item_name"     : item.item_name,
        "description"   : item.description if item.description else "",
        "lost_location" : item.lost_location,
        "contact_name"  : item.contact_name,
        "contact_phone" : item.contact_phone,
        "contact_email" : item.contact_email,
        "image_url"     : f"/uploads/{item.image_path}" if item.image_path else None,
        "created_at"    : iso_date,
    }