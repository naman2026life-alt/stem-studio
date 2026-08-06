from __future__ import annotations

import shutil
from pathlib import Path

import gradio as gr

from stem_studio.audio import mix_tracks, run_demucs, session_dir

APP_DIR = session_dir()


def separate(source: str | None, progress=gr.Progress()):
    if not source:
        raise gr.Error("Choose a song first.")
    progress(0.05, desc="Preparing audio…")
    try:
        outputs = run_demucs(source, APP_DIR / "separated")
    except (ValueError, RuntimeError) as exc:
        raise gr.Error(str(exc)) from exc
    progress(1, desc="Separation complete")
    return *(str(outputs[key]) for key in ("instrumental", "drums", "vocals")), str(outputs["instrumental"])


def create_mix(instrumental, vocal, offset, trim_start, trim_end, vocal_gain, instrumental_gain):
    if not instrumental or not vocal:
        raise gr.Error("Add both an instrumental and a recorded vocal track.")
    try:
        wav, mp3 = mix_tracks(
            instrumental, vocal, APP_DIR / "mixes", round(offset), trim_start, trim_end,
            vocal_gain, instrumental_gain,
        )
    except (ValueError, RuntimeError, OSError) as exc:
        raise gr.Error(f"Mixing failed: {exc}") from exc
    return str(wav), str(wav), str(mp3)


def cleanup():
    shutil.rmtree(APP_DIR, ignore_errors=True)


CSS = """
.gradio-container {max-width: 1050px !important; margin: auto !important;}
.hero {text-align:center; margin: 1.5rem 0 2rem;}
.hero h1 {font-size: 2.35rem; margin-bottom:.35rem;}
.hero p {color: #737373; font-size: 1.05rem;}
"""

with gr.Blocks(title="Stem Studio") as demo:
    gr.HTML("<div class='hero'><h1>🎛️ Stem Studio</h1><p>Private, local stem separation and vocal mixing.</p></div>")
    with gr.Tab("1 · Separate a song"):
        gr.Markdown("Your audio stays on this computer. The first run downloads the Demucs model.")
        source = gr.Audio(label="Source song", type="filepath", sources=["upload"])
        separate_btn = gr.Button("Separate stems", variant="primary", size="lg")
        with gr.Row():
            instrumental = gr.Audio(label="Instrumental · no vocals", type="filepath")
            drums = gr.Audio(label="Drums only", type="filepath")
            vocals = gr.Audio(label="Vocals only", type="filepath")
        instrumental_state = gr.State()
        separate_btn.click(separate, source, [instrumental, drums, vocals, instrumental_state], show_progress="full")

    with gr.Tab("2 · Mix your vocals"):
        gr.Markdown("Use the separated instrumental automatically, or upload another backing track.")
        with gr.Row():
            mix_instrumental = gr.Audio(label="Instrumental", type="filepath", sources=["upload"])
            recorded_vocal = gr.Audio(label="Your isolated vocal", type="filepath", sources=["upload", "microphone"])
        use_separated = gr.Button("Use separated instrumental")
        use_separated.click(lambda path: path, instrumental_state, mix_instrumental)
        with gr.Accordion("Timing and levels", open=True):
            offset = gr.Slider(-10000, 30000, value=0, step=10, label="Vocal offset (ms)", info="Positive = vocal starts later; negative = skip the beginning of the vocal")
            with gr.Row():
                trim_start = gr.Number(value=0, minimum=0, label="Trim vocal start (seconds)")
                trim_end = gr.Number(value=0, minimum=0, label="Trim vocal end (seconds; 0 = keep all)")
            with gr.Row():
                vocal_gain = gr.Slider(-24, 12, value=0, step=.5, label="Vocal gain (dB)")
                instrumental_gain = gr.Slider(-24, 6, value=-3, step=.5, label="Instrumental gain (dB)")
        mix_btn = gr.Button("Create mix", variant="primary", size="lg")
        mixed_preview = gr.Audio(label="Mixed preview", type="filepath")
        with gr.Row():
            wav_download = gr.File(label="Download WAV")
            mp3_download = gr.File(label="Download MP3")
        mix_btn.click(create_mix, [mix_instrumental, recorded_vocal, offset, trim_start, trim_end, vocal_gain, instrumental_gain], [mixed_preview, wav_download, mp3_download])

    gr.Markdown("Built with Demucs · FFmpeg · Gradio. Audio and models remain local.")

if __name__ == "__main__":
    demo.launch(inbrowser=True, allowed_paths=[str(APP_DIR)], css=CSS, theme=gr.themes.Soft())
