# Stem Studio

A private, local-first Mac app for turning songs into useful practice tracks and mixing your own vocals back in. Nothing is uploaded to a cloud service.

## What it does

- Accepts MP3, WAV, M4A, FLAC, AAC, and OGG audio.
- Creates **instrumental/no-vocals**, **drums-only**, and **vocals-only** tracks with Demucs.
- Previews and downloads each stem in the browser.
- Mixes a recorded/uploaded vocal with the instrumental.
- Adjusts vocal timing (including negative offsets), trims the vocal, and controls both track levels.
- Previews the result and exports lossless WAV plus 320 kbps MP3.

## macOS setup (Apple Silicon)

Open Terminal and run:

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

The app opens at `http://127.0.0.1:7860`. Stop it with Control-C. Intel Macs can replace `/opt/homebrew/bin/python3.11` with `python3.11`.

## First run and performance

Demucs downloads its model on the first separation (roughly a few hundred MB) and caches it locally. Separation is compute-heavy. PyTorch support and song length determine speed; Apple Silicon works locally, but a several-minute song can take several minutes. Stem separation is excellent for an open-source model but is not artifact-free.

Timing is deliberately manual in this MVP. Use the offset in 10 ms increments, trim any count-in/silence, and adjust gains while previewing. Automatic alignment is not included because a solo vocal and an instrumental often lack enough shared signal for cross-correlation to be dependable.

## Architecture

```text
Browser UI (Gradio)
    ├── separation → Demucs CLI → vocals / drums / no_vocals WAV
    └── mixing → pydub + FFmpeg → preview / WAV / MP3
```

Each launch uses an OS temporary session directory. Files are available while the app is running and are removed by the operating system later. Model weights are cached in the ignored local `.model-cache` directory so subsequent runs can reuse them. No music or model weights are committed.

## Test

```bash
pip install -r requirements-dev.txt
pytest
```

Tests synthesize tones locally; no copyrighted audio is included.

## License

MIT. Demucs and its model have their own licenses; review them for your intended use.
