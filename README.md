# Stem Studio

Stem Studio accepts audio or video, can extract a video's audio as MP3, keeps and joins multiple time ranges, and turns the active audio into **instrumental/no-vocals**, **drums-only**, and **vocals-only** tracks. It also includes a local mixer for placing a separately recorded vocal over the instrumental with offset, trim, and gain controls.

## Where it lives

- Source: <https://github.com/naman2026life-alt/stem-studio>
- Local app: Gradio on your Mac at `http://127.0.0.1:7860` while `python app.py` is running.
- Hosted interface: Next.js in `web/`, deployed to Vercel.
- Hosted processing: `modal_app.py`, designed for temporary GPU jobs on Modal.
- Portable processing API: `processor/api.py` and `Dockerfile.processor` for any suitable long-running container host.

## Why there is no Supabase

The MVP is a simple upload → process → download flow. It does not need accounts, a database, or a permanent audio library, so Supabase would add setup and retention complexity without improving the core workflow.

The hosted stack is intentionally lean:

```text
Vercel (Next.js interface)
  └── short-lived signed upload request
                    │ direct media upload
                    ▼
Modal (temporary FFmpeg tools + Demucs on a T4 GPU)
  ├── video → MP3 (optional; CPU)
  ├── ordered trim + merge → MP3 (optional; CPU)
  ├── one four-stem pass (GPU)
  ├── vocals.wav
  ├── drums.wav
  ├── instrumental.wav (drums + bass + other)
  └── compact 192 kbps MP3 copies prepared for mobile sharing
                    │
                    └── preview/save/share; temporary tool files are deleted
                        immediately and stem jobs after one hour
```

Vercel serves the interface but does not run Demucs. A full song can exceed the duration, memory, and package constraints of a free web function. Modal scales the processor to zero between jobs and provides $30/month of compute credit on its free Starter plan at the time this architecture was chosen.

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

Open <http://127.0.0.1:7860>. The first separation downloads the Demucs model into the ignored `.model-cache` directory. Stem Studio runs one four-stem pass, then recombines drums, bass, and other to create the instrumental.

## Run the Vercel interface and processor locally

Terminal 1:

```bash
source .venv/bin/activate
pip install -r requirements-processor.txt
uvicorn processor.api:app --host 127.0.0.1 --port 8000
```

Terminal 2:

```bash
cd web
cp .env.example .env.local
npm install
npm run dev
```

Open <http://localhost:3000>. For a local-only run, the shared secret may be left blank. Set the same `PROCESSOR_SHARED_SECRET` on both services before exposing the processor publicly.

## Deploy the processor to Modal

```bash
source .venv/bin/activate
pip install -r requirements-modal.txt
modal setup

# Generate a value once, then set the same value in Vercel.
python -c 'import secrets; print(secrets.token_urlsafe(32))'
modal secret create stem-studio-upload-secret PROCESSOR_SHARED_SECRET=PASTE_GENERATED_VALUE

modal deploy modal_app.py
```

The deploy command prints the public processor URL. The first real isolation job downloads the `htdemucs` model into the persistent `stem-studio-models` volume. Stem jobs are stored in a separate temporary volume and cleaned up after one hour. Video conversion and trim/merge use FFmpeg in temporary container storage and delete their files as soon as the response finishes.

## Deploy the interface to Vercel

Import this GitHub repository with the project root set to `web`, then add:

```text
NEXT_PUBLIC_PROCESSOR_URL=https://YOUR-MODAL-WEB-URL
PROCESSOR_SHARED_SECRET=THE-SAME-GENERATED-VALUE
STEM_STUDIO_PASSWORD=A-SEPARATE-STRONG-PERSONAL-PASSWORD
```

Redeploy after adding or changing `NEXT_PUBLIC_PROCESSOR_URL`, because public Next.js variables are embedded at build time.

`STEM_STUDIO_PASSWORD` keeps the upload-token endpoint private so an unknown visitor cannot spend your compute credit. The GPU worker is also capped at one container and shuts down after 15 idle seconds.

## Portable Docker processor

If Modal is not desired, the same API can run on a container service with enough memory and a request/job lifetime of at least 30 minutes:

```bash
docker build -f Dockerfile.processor -t stem-studio-processor .
docker run --rm -p 8000:8000 \
  -e ALLOWED_ORIGINS=https://YOUR-VERCEL-DOMAIN \
  -e PROCESSOR_SHARED_SECRET=YOUR_SHARED_SECRET \
  stem-studio-processor
```

Use one container worker. The portable API keeps temporary job state in its local filesystem and is meant for a single-user MVP, not horizontal scaling.

## Hosted workflow and supported media

- Audio inputs: MP3, WAV, M4A, FLAC, AAC, OGG, and browser-recorded WEBM.
- Video inputs: MP4, MOV, M4V, MKV, WEBM, and AVI. Extracting the first audio track to a 320 kbps MP3 is optional.
- Built-in recorder: capture a new take from the device microphone, pause/resume, stop, preview, save, share, trim, or isolate it without first exporting from another app. iPhone recordings use Safari's AAC/MP4 support; other browsers can use WEBM/Opus.
- Existing iPhone recordings: Safari and Chrome use the native Files picker. Voice Memos normally shares recordings as M4A, which is accepted directly after the user exports it through the iOS Share sheet.
- Home Screen app: in iPhone Safari, use **Share → Add to Home Screen**. The web-app manifest, standalone display mode, theme, and Apple touch icon are included.
- Upload limit: 150 MB per operation.
- Trim and merge: add up to 50 ordered parts using raw seconds, `MM:SS`, or `HH:MM:SS`. An empty end uses the rest of the file; values beyond the duration are capped at the end.
- Mobile duration picker: tapping Start or End opens touch-scroll wheels for hours, minutes, and seconds, plus an exact **End of track** shortcut. Desktop typing remains available.
- The uploaded, recorded, converted, or trimmed file becomes the active audio and can be previewed, saved to the device, shared through the native share sheet, edited again, or isolated.
- Outputs: instrumental/no-vocals, drums, and vocals as WAV, with browser preview, **Save to device**, and **Share to WhatsApp** actions. Compact 192 kbps MP3 copies are prepared in the background so an iPhone can open its share sheet with one tap without first fetching a full WAV. The full-quality WAV remains the saved output.
- Local mixing: manual vocal offset, trim start/end, vocal gain, instrumental gain, WAV preview, and WAV/320 kbps MP3 export.
- Hosted retention: no account or permanent library; source and outputs expire after one hour.

## Validation

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
python -m py_compile modal_app.py processor/api.py

cd web
npm run lint
npm run build
```

Tests use generated tones; no copyrighted music or model weights are committed.

## Current limitations

- The first hosted job is slower because it downloads and caches the Demucs model and starts a new compute container.
- Separation speed varies with song length and available hardware; a five-minute song is typically several minutes on CPU and materially faster on a T4 GPU.
- Demucs can leave vocal bleed or musical artifacts, especially on dense mixes.
- Video conversion uses the first audio track. Videos without audio cannot be converted.
- Trim/merge is lossily exported as a 320 kbps MP3; repeated edits re-encode the active audio, so it is better to describe all desired parts in one merge when possible.
- iOS does not let a website read another app's private recordings, and WebKit does not currently support receiving shared files through the Web Share Target API. Use the built-in recorder for a no-export workflow, or the source app's Share/Export action for an existing recording.
- A website cannot silently choose a WhatsApp recipient. **Share to WhatsApp** opens the operating system share sheet with the file attached; the user must select WhatsApp and the destination chat. Browsers without file-sharing support save the file so it can be attached manually.
- Hosted jobs are intentionally temporary. Refresh recovery works within the same browser session, but there is no long-term history.
- Vocal recording/mixing is currently in the local Gradio app; the first hosted release focuses on the two highest-priority outputs: no-vocals and drums-only.

## License

MIT. Demucs and its model have their own licenses; review them for your intended use.
