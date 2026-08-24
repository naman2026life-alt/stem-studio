# Stem Studio

Stem Studio turns a song into **instrumental/no-vocals**, **drums-only**, and **vocals-only** tracks, then lets you mix a separate vocal recording over the instrumental. The repository contains both the original local app and a cloud-ready web stack.

## Where it lives

- Source: `https://github.com/naman2026life-alt/stem-studio`
- Local app: Gradio on your Mac; it exists only while `python app.py` is running.
- Cloud web app: Next.js in `web/`, designed for Vercel.
- Cloud data: Supabase Auth, Postgres job records, and a private Storage bucket.
- Separation compute: `worker.py`, initially run on a Mac or a dedicated worker host. Demucs is intentionally not placed in a Vercel or Supabase function.

## Architecture

```text
Vercel (Next.js UI)
  ├── Google login via Supabase Auth
  ├── resumable private uploads to Supabase Storage
  └── job status + signed downloads through Supabase RLS
                     │
                     ▼
Supabase (Postgres + private Storage)
                     │ queued jobs
                     ▼
Python worker (Demucs + FFmpeg)
  └── one four-stem pass → vocals / drums / recombined no-vocals
```

Supabase is useful in the hosted version because it owns identity, private files, and durable job state. It is not needed for the original single-user local app. Demucs is too CPU-, memory-, and duration-heavy for ordinary serverless functions, so the worker remains a separate process.

## Local Gradio app (Apple Silicon)

```bash
brew install ffmpeg python@3.11
git clone https://github.com/naman2026life-alt/stem-studio.git
cd stem-studio
/opt/homebrew/bin/python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python app.py
```

Open `http://127.0.0.1:7860`. The first separation downloads the Demucs model into the ignored `.model-cache` directory. A full four-stem pass is used once, then bass, drums, and other are recombined to create the instrumental.

## Cloud setup

### 1. Create and migrate Supabase

Create a Supabase project, then link and apply the committed migration:

```bash
npx supabase login
npx supabase link --project-ref YOUR_PROJECT_REF
npx supabase db push
```

The migration creates `separation_jobs`, enables RLS, grants least-privilege client access, and creates a private `audio` bucket with per-user storage policies.

In Supabase Auth, enable Google and add these redirect URLs:

```text
http://localhost:3000/auth/callback
https://YOUR_VERCEL_DOMAIN/auth/callback
```

### 2. Run the Vercel web app locally

```bash
cd web
cp .env.example .env.local
# Fill in NEXT_PUBLIC_SUPABASE_URL and NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
npm install
npm run dev
```

Open `http://localhost:3000`. Only the publishable key belongs in the web app; never add a Supabase secret key to a `NEXT_PUBLIC_` variable.

### 3. Run the separation worker

```bash
cd ..
source .venv/bin/activate
pip install -r requirements-worker.txt
cp .env.worker.example .env.worker
# Fill in SUPABASE_URL and the server-only SUPABASE_SECRET_KEY
set -a; source .env.worker; set +a
python worker.py
```

Keep the secret key only on the worker host. The worker polls queued jobs, downloads the private source, runs Demucs, uploads WAV stems, and records completion or failure.

### 4. Deploy `web/` to Vercel

Import this GitHub repository, set the project root directory to `web`, and add:

```text
NEXT_PUBLIC_SUPABASE_URL
NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
```

Deploy, then add the final Vercel callback URL in Supabase Auth.

## Supported audio and controls

- Inputs: MP3, WAV, M4A, FLAC, AAC, and OGG.
- Outputs: instrumental/no-vocals, drums, and vocals as WAV.
- Local mixing: manual vocal offset, trim start/end, vocal gain, instrumental gain, WAV preview, and WAV/320 kbps MP3 export.
- Cloud uploads: resumable 6 MB chunks, private bucket, 150 MB per-file limit.

## Validation

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest
cd web
npm run lint
npm run build
```

Tests use synthetic tones; no copyrighted music or model weights are committed.

## Current limitations

- The web app needs a running worker. For always-on public use, deploy `worker.py` to a dedicated CPU/GPU service.
- Supabase Free includes 1 GB of file storage, so delete old audio or add retention cleanup before inviting many users.
- Separation can produce audible artifacts and speed depends on the worker hardware.
- The local app contains vocal mixing controls; the first hosted interface focuses on secure separation jobs and downloads.

## License

MIT. Demucs and its model have their own licenses; review them for your intended use.
