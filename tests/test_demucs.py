from pathlib import Path
from unittest.mock import Mock, patch

from pydub import AudioSegment

from stem_studio.audio import run_demucs


def test_demucs_uses_project_local_model_cache(tmp_path: Path):
    source = tmp_path / "song.wav"
    source.touch()
    stem_dir = tmp_path / "output" / "htdemucs" / "song"

    def fake_run(command, **kwargs):
        stem_dir.mkdir(parents=True, exist_ok=True)
        for name in ("vocals.wav", "drums.wav", "bass.wav", "other.wav"):
            AudioSegment.silent(duration=100).export(stem_dir / name, format="wav")
        assert kwargs["env"]["TORCH_HOME"].endswith("stem-studio/.model-cache")
        assert "--two-stems" not in command
        return Mock(returncode=0, stdout="", stderr="")

    with patch("stem_studio.audio.subprocess.run", side_effect=fake_run) as run:
        outputs = run_demucs(source, tmp_path / "output")

    assert run.call_count == 1
    assert outputs["instrumental"].name == "no_vocals.wav"
    assert outputs["instrumental"].exists()
    assert outputs["drums"].name == "drums.wav"
