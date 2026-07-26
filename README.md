# VibeItStudio Render Worker

Worker GitHub Actions untuk membaca snapshot/timeline Firestore, mengunduh media asli dari Backblaze B2, merender video dengan FFmpeg, mengunggah hasil akhir ke B2, lalu memperbarui status Firestore.

## Status versi ini

Sudah berfungsi untuk:

- text element: posisi, ukuran font, warna, bold, italic, opacity, wrapping;
- image/video element: posisi, ukuran, opacity, rotasi, center crop;
- video/audio: `trimStart`, speed, mute, volume;
- background music, `loopStart`, dan `musicVolume` per slide;
- standard transition dan custom transition slide;
- output 480p, 720p, dan 1080p;
- snapshot immutable dari B2 dengan fallback `rooms/{roomId}/timeline/current`;
- upload final MP4 ke B2;
- status `render_jobs`, `final_media/current`, Room, dan public Receiver;
- sweeper B2 yang aman dan dry-run secara default;
- validasi schema, ukuran timeline, ukuran media, ID, dan B2 path;
- concurrency per Room agar dua Bake Room yang sama tidak berjalan bersamaan.

Belum pixel-perfect untuk:

- rotasi text;
- rounded corner media;
- crop transform bebas dari editor;
- efek transition khusus yang belum dipetakan. Efek tersebut memakai cross-fade aman.

Worker akan tetap menghasilkan video, tetapi tiga detail tersebut perlu fase renderer fidelity tersendiri jika hasil FFmpeg harus identik sampai tingkat pixel dengan Canvas browser.

## Repository secrets

Tambahkan di **Settings → Secrets and variables → Actions → Repository secrets**:

```text
FIREBASE_SERVICE_ACCOUNT
B2_KEY_ID
B2_APPLICATION_KEY
B2_BUCKET_NAME
```

`FIREBASE_SERVICE_ACCOUNT` berisi seluruh JSON service account, bukan path file.

## Repository variables opsional

```text
WORKER_FFMPEG_PRESET=medium
WORKER_MAX_MEDIA_BYTES=2147483648
WORKER_MAX_TIMELINE_BYTES=921600

SWEEPER_ENABLED=false
SWEEPER_APPLY=false
SWEEPER_MAX_ROOMS=20
SWEEPER_DELETE_FINAL=false
SWEEPER_FINAL_RETENTION_DAYS=30
SWEEPER_PRUNE_FIRESTORE_METADATA=false
```

Jangan aktifkan `SWEEPER_ENABLED=true` dan `SWEEPER_APPLY=true` sebelum dry-run manual sudah diperiksa. Keduanya sengaja dipisah sebagai pengaman ganda.

## Trigger Bake dari Vercel

Environment Vercel:

```env
GITHUB_WORKER_OWNER=noitsume
GITHUB_WORKER_REPO=vibe-it-studio-worker
GITHUB_WORKER_WORKFLOW_FILE=render.yml
GITHUB_WORKER_REF=main
```

Dispatch body:

```json
{
  "ref": "main",
  "inputs": {
    "room_id": "room_123",
    "job_id": "job_123",
    "resolution": "720"
  }
}
```

`job_id` sangat disarankan. Endpoint Bake sebaiknya membuat `render_jobs/{jobId}` dan snapshot JSON immutable terlebih dahulu, baru memicu workflow.

## Kontrak Firestore yang dibaca

```text
rooms/{roomId}
rooms/{roomId}/timeline/current
rooms/{roomId}/media/{mediaId}
render_jobs/{jobId}
```

Jika `render_jobs/{jobId}.snapshotPath` tersedia, worker mengambil JSON tersebut dari B2 dan tidak memakai timeline yang mungkin sudah berubah.

## Firestore write per Bake

Worker tidak menulis progress per persen.

- job manual yang belum ada: 1 create;
- mulai render: 1 write;
- sukses: satu batch berisi 3 atau 4 document writes;
- gagal: 1 write status failed.

Sukses normal dengan job yang sudah dibuat endpoint: sekitar 4–5 document writes, bukan ribuan write.

## B2 output

```text
rooms/{roomId}/final/{jobId}.mp4
```

Firestore `rooms/{roomId}/final_media/current` menunjuk file terbaru.

## Tes lokal tanpa credential

```bash
python -m pip install -r requirements.txt
python render.py \
  --timeline-file examples/timeline.sample.json \
  --resolution 480 \
  --output output/sample.mp4
```

Menjalankan unit dan integration test:

```bash
python -m pytest -q
```

## Sweeper

Manual dry-run melalui tab Actions atau lokal:

```bash
SWEEPER_APPLY=false python sweeper.py
```

Menghapus sungguhan:

```bash
SWEEPER_APPLY=true python sweeper.py
```

Default hanya menghapus file mentah B2 di prefix media, submissions, dan snapshots. Final video dan metadata Firestore tidak dihapus kecuali variabel opsionalnya diaktifkan.
