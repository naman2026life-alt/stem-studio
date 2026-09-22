# Stem Studio

Stem Studio accepts audio or video, can privately import permitted audio from a single YouTube video, can extract a video's audio as MP3, keeps and joins multiple time ranges, and includes a dedicated **Karaoke mode** that removes detected vocals and returns one backing track. It can also split the active audio into **instrumental/no-vocals**, **drums-only**, and **vocals-only** tracks, then mix a separately recorded vocal over an instrumental with offset, trim, and gain controls.

**Singing coach** adds a continuous pitch graph for your recording and a reference comparison for practicing a song. Record or upload a short phrase, inspect the detected fundamental and note entries, then compare your take with a solo vocal or a selected passage from a full song.

## Singing coach

1. Open **Singing coach** in the hosted app. Record a take, upload audio, or use the active audio from the editor.
2. Choose a short section (15 seconds by default, up to 90 seconds). For a full song, choose the start of the phrase you want to practice and select the input with backing music; the processor isolates that section's vocal first. Existing vocal stems can be reused.
3. Analyze the take to see time on the horizontal axis and musical pitch on the vertical axis. The trace is continuous: bends, vibrato, and note attacks are preserved. Gaps indicate silence or uncertain pitch. The optional raw trace exposes the detector's unsmoothed estimate.
4. Add and analyze the same phrase from a reference song. Compare the contours, hear the selected audio, and inspect pitch differences in cents (100 cents = one semitone). Automatic alignment changes only the start time. Manual offset and an explicit key/octave adjustment are available.
5. Choose your **Sa** if you want relative Indian note labels. A recording cannot uniquely establish the tonic, so the app does not silently guess it.

The score measures **pitch matching to this reference**, not vocal beauty, diction, rhythm proficiency, or raga correctness. Missing reference notes lower the score. Silence and unreliable matches do not earn a score. A repeated melody, steady note, or very different performance can make automatic timing ambiguous; use manual alignment in those cases. The app never silently fixes the singer's key or octave and never time-warps mistakes away.

### Pitch analysis design

- [CREPE full](https://github.com/maxrmorrison/torchcrepe), through pinned `torchcrepe==0.0.24`, estimates one monophonic fundamental every 10 ms. Model weights ship with that dependency and are not committed here.
- A sequence decoder uses the model's pitch evidence across the complete clip, with a finite cost for genuine large note jumps. This avoids inference-batch seams and permits real octave changes.
- Model confidence and signal level reject uncertain/noisy frames. An independent waveform-periodicity check challenges octave/third-harmonic estimates when a lower fundamental has stronger acoustic support; brief corrections also require matching surrounding notes. Conservative short-glitch repair and a 30 ms median suppress excursions without snapping frequencies onto equal-tempered notes or bridging breaths.
- Sustained-note descriptions summarize pitch stability and the first part of each note. The plotted curve remains the measured performance.
- Comparison searches for a single time offset, reports ambiguity, and measures signed/absolute pitch error against the reference's actual contour. No dynamic time warping or automatic key correction is used.
- A matched frame earns full credit within 25 cents, decreases to 50 at 100 cents, and to zero at 300 cents. Missing reference frames earn zero. Scores require at least one second of matched clear singing and at least 30% reference coverage. These are transparent practice heuristics, not a validated assessment standard.

The shared API implementation is `stem_studio/practice_jobs.py`; pitch analysis is `stem_studio/pitch.py`; alignment and scoring are `stem_studio/pitch_compare.py`. Both the portable API and Modal expose `POST /practice/jobs`, `GET /practice/jobs/{id}`, `GET /practice/jobs/{id}/audio`, and `POST /practice/compare`. Mutating endpoints use the existing private studio token. Temporary analysis and playback links expire after one hour. Only the requested section enters the model worker; uploaded originals are discarded after clipping. On Modal, practice and separation share one T4 worker; the private Mac service runs the same workflows locally with bounded CPU resources.

### Reproduce the audio checks

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
python scripts/benchmark_pitch.py

# Optional: generates short synthetic audio and exercises the deployed API.
# This uses the existing Modal secret internally and spends a little compute.
modal run scripts/verify_practice_live.py
```

Ordinary tests do not download or run a neural model. The benchmark runs the real model against generated ground truth: dominant overtones, a missing fundamental, noise/silence, vibrato, note changes, octave jumps, quiet audio, and scooped attacks. These controlled cases do not establish accuracy on every singer or recording. For best results, use a single clear voice; harmonies, heavy reverb, accompaniment leakage, and extreme vocal effects remain difficult. Analysis runs after recording stops, rather than as a live microphone tuner.

See [validation and known limitations](docs/singing-coach-validation.md) for the real-singer development benchmark, score safeguards, and release checks. Video uploads with audio are supported; the selected audio section is extracted before analysis.

## Where it lives

- Source: <https://github.com/naman2026life-alt/stem-studio>
- Private Mac interface: the full Next.js app at `http://127.0.0.1:3007`, with its authenticated processor at `http://127.0.0.1:8766`, after installing the optional login services. See [Mac setup and troubleshooting](docs/mac-processor.md).
- Original local app: Gradio at `http://127.0.0.1:7860` while `python app.py` is running.
- Hosted interface: Next.js in `web/`, deployed to Vercel.
- Cloud processing option: `modal_app.py`, designed for temporary GPU jobs on Modal.
- Portable processing API: `processor/api.py` and `Dockerfile.processor` for any suitable long-running container host.

The Mac option needs no Modal payment method or GPU credits for local processing. Phone access can retain the Vercel interface and send jobs to the Mac through an authenticated HTTPS tunnel. This requires a separate tunnel setup and Vercel redeployment: installing the Mac services does **not** switch the live website automatically. The Mac must be awake, connected, and logged in. The [Mac guide](docs/mac-processor.md) covers the boundary between local readiness and a completed remote cutover.

## Why there is no Supabase

The MVP is a simple upload → process → download flow. It does not need accounts, a database, or a permanent audio library, so Supabase would add setup and retention complexity without improving the core workflow.

The original Modal-hosted option is intentionally lean; the Mac service replaces the processing layer without adding a database:

```text
Vercel (Next.js interface)
  └── short-lived signed upload request
                    │ direct media upload
                    ▼
Modal (temporary FFmpeg tools + Demucs on a T4 GPU)
  ├── permitted YouTube link → queued 192 kbps MP3 import (CPU)
  │          └── if YouTube blocks the cloud IP, a private Mac/PC helper
  │              downloads over the home connection and returns the MP3
  ├── video → MP3 (optional; CPU)
  ├── ordered trim + merge → MP3 (optional; CPU)
  ├── instrumental + recorded vocal → WAV + MP3 mix (CPU)
  ├── Karaoke mode → karaoke.wav (drums + bass + other)
  ├── or full split → instrumental.wav + drums.wav + vocals.wav
  ├── singing phrase → optional vocal isolation → pitch contour + note entries
  ├── two analysed phrases → timing alignment + pitch comparison
  └── compact 192 kbps MP3 copies prepared for mobile sharing
                    │
                    └── preview/save/share; temporary tool files are deleted
                        immediately and queued jobs after one hour
```

Vercel serves the interface but does not run Demucs. A full song can exceed the duration, memory, and package constraints of a free web function. Modal scales the processor to zero between jobs. Credits and pricing can change, so check the current usage and spending-limit controls in the Modal dashboard before running many separation jobs.

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
brew install deno ffmpeg
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

For the full interface with authenticated processing and automatic restart at Mac login, use the [private Mac service setup](docs/mac-processor.md) instead. It uses ports 3007/8766, stores the shared secret in Keychain, and keeps the simpler development workflow above unchanged.

## Deploy the processor to Modal

```bash
source .venv/bin/activate
pip install -r requirements-modal.txt
modal setup

# Generate a value once, then set the same value in Vercel.
python -c 'import secrets; print(secrets.token_urlsafe(32))'
modal secret create stem-studio-upload-secret PROCESSOR_SHARED_SECRET=PASTE_GENERATED_VALUE

# Separate least-privilege credential used only by the outbound home helper.
python -c 'import secrets; print(secrets.token_urlsafe(48))'
modal secret create stem-studio-home-worker-secret HOME_WORKER_SECRET=PASTE_SECOND_GENERATED_VALUE

modal deploy modal_app.py
```

The deploy command prints the public processor URL and a separate `home-worker-api` URL. The helper credential never belongs in Vercel, browser JavaScript, GitHub, or any `NEXT_PUBLIC_*` variable. The image includes pinned yt-dlp, its EJS support package, and a checksum-verified Deno runtime for current YouTube extraction. The first real isolation job downloads the `htdemucs` model into the persistent `stem-studio-models` volume. Stem and YouTube import jobs use the temporary data volume and are cleaned up after one hour. Video conversion and trim/merge use FFmpeg in temporary container storage and delete their files as soon as the response finishes.

## Private Mac/Windows YouTube helper

YouTube sometimes rejects downloads from Modal's datacenter IP even when the same public video works normally at home. Stem Studio first tries the hosted route (including an embedded-player retry), then changes the job to **Waiting for your private helper** only for that specific network block. The helper makes outbound HTTPS requests; it does not open a laptop port, change router settings, use browser cookies, or require Supabase.

On the Mac that should take over automatically:

```bash
brew install ffmpeg deno python@3.11
source .venv/bin/activate
pip install -r requirements-worker.txt

# One-time foreground check (the secret can be entered without being shown):
python -m stem_studio.home_worker \
  --broker-url https://YOUR-MODAL-HOME-WORKER-API-URL \
  --once

# Install at login; the secret is saved in macOS Keychain, not the repository.
scripts/install_macos_helper.sh https://YOUR-MODAL-HOME-WORKER-API-URL
```

The launch agent checks about once every three minutes (about 90 seconds average pickup time) and runs one import at a time. Its broker is a separate tiny Modal function with a two-second scale-down window, so idle polling does not keep the main processor warm. View its local status with:

```bash
launchctl print "gui/$(id -u)/com.stemstudio.youtube-helper"
tail -f "$HOME/Library/Logs/StemStudioHelper.log"
```

On Windows, install Python 3.11, FFmpeg, and Deno, then run the same module with the secret in the current PowerShell session:

```powershell
py -3.11 -m venv .venv-worker
.\.venv-worker\Scripts\Activate.ps1
pip install -r requirements-worker.txt
$env:STEM_STUDIO_WORKER_SECRET = "YOUR_PRIVATE_HELPER_SECRET"
python -m stem_studio.home_worker --broker-url https://YOUR-MODAL-HOME-WORKER-API-URL
```

For an unattended Windows Task Scheduler job, store the secret in Windows Credential Manager and have a short local wrapper retrieve it; do not put the secret in GitHub or a public task definition. The helper must be awake and online when a cloud-blocked job is waiting. It uses a renewable, one-job lease so a restart or short network interruption cannot let an old attempt overwrite a newer result.

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
- Private YouTube import: expand **Import audio from a YouTube link**, paste one `youtube.com` or `youtu.be` video link, confirm that you own it or have permission/authorization to download it, and enter the same private studio password used for processing. A queued CPU job creates a 192 kbps MP3 and makes it the active audio automatically. If YouTube blocks Modal, the installed private Mac/PC helper takes over without another action on the phone. The browser remembers the active import ID and reconnects after an iPhone reload or app relaunch while the one-hour job still exists.
- YouTube safety limits: public single-video links only; no playlists, live/upcoming streams, account cookies, or videos over 20 minutes. The downloaded source is capped at 50 MB and the finished MP3 at 40 MB. Public availability by itself is not permission to download a video; follow YouTube's terms and the rightsholder's permissions.
- Video inputs: MP4, MOV, M4V, MKV, WEBM, and AVI. Extracting the first audio track to a 320 kbps MP3 is optional.
- Built-in recorder: capture a new take from the device microphone, pause/resume, stop, preview, save, share, trim, or isolate it without first exporting from another app. iPhone recordings use Safari's AAC/MP4 support; other browsers can use WEBM/Opus.
- Existing iPhone recordings: Safari and Chrome use the native Files picker. Voice Memos normally shares recordings as M4A, which is accepted directly after the user exports it through the iOS Share sheet.
- Home Screen app: in iPhone Safari, use **Share → Add to Home Screen**. The web-app manifest, standalone display mode, theme, and Apple touch icon are included.
- Upload limit: 150 MB per operation.
- Separation and mixing limit: each input must be 30 minutes or shorter; separation audio is validated before GPU work is queued. Trim inputs may be up to 60 minutes.
- Trim and merge: add up to 50 ordered parts using raw seconds, `MM:SS`, or `HH:MM:SS`. An empty end uses the rest of the file; values beyond the duration are capped at the end. The combined output is capped at 60 minutes to prevent accidental runaway files.
- Mobile duration picker: tapping Start or End opens touch-scroll wheels for hours, minutes, and seconds, plus an exact **End of track** shortcut. Desktop typing remains available.
- The uploaded, recorded, converted, or trimmed file becomes the active audio and can be previewed, saved to the device, shared through the native share sheet, edited again, or isolated.
- Karaoke mode: one prominent action removes detected vocals and returns a `*-karaoke.wav` backing track. The full-split option remains available for instrumental/no-vocals, drums, and vocals.
- Every output includes browser preview, **Save to device**, and **Share to WhatsApp** actions. Compact 192 kbps MP3 copies are prepared in the background so an iPhone can open its share sheet with one tap without first fetching a full WAV. The full-quality WAV remains the saved output.
- Vocal mixing: choose a still-available instrumental result or upload another backing track, add a separately recorded vocal (or use the active recording), then adjust manual offset, vocal trim, vocal gain, and instrumental gain. Preview the result and export WAV or 320 kbps MP3.
- Hosted retention: no account or permanent library; source, stem, and mix outputs expire after one hour.

## Validation

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
ruff check app.py modal_app.py processor stem_studio tests
python -m py_compile modal_app.py processor/api.py

cd web
npm run lint
npm run build
```

Tests use generated tones; no copyrighted music or model weights are committed.

## Current limitations

- The first hosted job is slower because it downloads and caches the Demucs model and starts a new compute container.
- Separation speed varies with song length and available hardware; a five-minute song is typically several minutes on CPU and materially faster on a T4 GPU.
- Karaoke mode returns only the no-vocals result and avoids unnecessary output files, but Demucs still estimates all sources internally, so GPU inference time and memory are similar to the full-split mode.
- Demucs can leave vocal bleed or musical artifacts, especially on dense mixes.
- Video conversion uses the first audio track. Videos without audio cannot be converted.
- Trim/merge is lossily exported as a 320 kbps MP3; repeated edits re-encode the active audio, so it is better to describe all desired parts in one merge when possible.
- iOS does not let a website read another app's private recordings, and WebKit does not currently support receiving shared files through the Web Share Target API. Use the built-in recorder for a no-export workflow, or the source app's Share/Export action for an existing recording.
- A website cannot silently choose a WhatsApp recipient. **Share to WhatsApp** opens the operating system share sheet with the file attached; the user must select WhatsApp and the destination chat. Browsers without file-sharing support save the file so it can be attached manually.
- Hosted jobs are intentionally temporary. Refresh recovery works within the same browser session, but there is no long-term history.
- YouTube changes its delivery and bot protection frequently. Private, members-only, age-restricted, and region-blocked videos remain unsupported. For otherwise usable videos that reject Modal's datacenter IP, the authenticated home helper retries over the Mac/PC's normal connection. Stem Studio never imports browser cookies or Google credentials. If both routes are rejected, upload an audio file you are authorized to use directly.

## License

MIT. Demucs and its model have their own licenses; review them for your intended use.
