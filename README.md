# BookLoop

A local, people-first book-swap app. Expo (React Native) frontend in `frontend/`, FastAPI + MongoDB
backend in `backend/`.

## Run it locally

### 1. Backend
Needs Python 3.11+ and a running MongoDB.

```powershell
cd backend
python -m venv .venv ; .\.venv\Scripts\Activate.ps1
pip install -r requirements.txt          # add -r requirements-dev.txt instead if you will run the tests
copy .env.example .env      # then edit: MONGO_URL, DB_NAME, and optionally the Google settings
# --host 0.0.0.0 lets a phone on the same Wi-Fi/hotspot reach it (default 127.0.0.1 does not)
python -m uvicorn server:app --host 0.0.0.0 --port 8001
```

The first start seeds a demo community. Uploaded images are stored in `backend/uploads/`
(`STORAGE_DIR` to change it).

### 2. Frontend
```powershell
cd frontend
copy .env.example .env      # set EXPO_PUBLIC_BACKEND_URL to your PC's address, e.g. http://192.168.1.10:8001
npm install
npx expo start -c           # -c clears the cache so a changed .env is picked up
```
Open the QR code with Expo Go. The phone and the PC must be on the same network, and Windows Firewall
must allow inbound connections to Python (8001) and Node (8081).

### Google sign-in
Optional. Without `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET` the Google button is hidden. Setup steps are
in `backend/.env.example`; Google requires an https redirect address, so a phone needs an https tunnel.

## Tests
```powershell
cd backend
$env:EXPO_PUBLIC_BACKEND_URL = "http://127.0.0.1:8001"   # use 127.0.0.1, not localhost (much faster on Windows)
python -m pytest tests -n 0
```
The tests create users and books, so point them at a throwaway database (`DB_NAME=bookloop_test`).
