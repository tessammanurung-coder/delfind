1. Install dependencies:
pip install -r requirements.txt

2. Jalankan backend (dari folder backend/):
python -m uvicorn main:app --reload --port 8000

3. Jalankan edge script (dari folder edge/):
python detector.py

4. Buka frontend:
python -m http.server 3000
# Buka → http://localhost:3000