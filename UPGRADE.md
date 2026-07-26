# Upgrade dari worker lama

1. Pertahankan folder `.git` lokal Anda.
2. Hapus file lama berikut:
   - `requirement.txt`
   - `render.py` kosong
   - workflow lama di `.github/workflows/`
3. Salin seluruh isi paket ini ke root repository worker.
4. Pastikan Repository Secrets tetap bernama:
   - `FIREBASE_SERVICE_ACCOUNT`
   - `B2_KEY_ID`
   - `B2_APPLICATION_KEY`
   - `B2_BUCKET_NAME`
5. Commit dan push.
6. Buka tab **Actions → Render Video Bake → Run workflow** untuk tes manual.

Paket ini tidak berisi `.git`, credential, service account, media final, cache Python, atau file sementara.
