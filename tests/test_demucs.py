from pathlib import Path
from unittest.mock import Mock, patch

from stem_studio.audio import run_demucs


def test_demucs_uses_project_local_model_cache(tmp_path: Path):
    source = tmp_path / "song.wav"
    source.touch()
    stem_dir = tmp_path / "output" / "htdemucs" / "song"

    def fake_run(command, **kwargs):
        stem_dir.mkdir(parents=True, exist_ok=True)
        (stem_dir / "vocals.wav").touch()
        (stem_dir / "no_vocals.wav").touch()
        (stem_dir / "drums.wav").touch()
        assert kwargs["env"]["TORCH_HOME"].endswith("stem-studio/.model-cache")
        return Mock(returncode=0, stdout="", stderr="")

    with patch("stem_studio.audio.subprocess.run", side_effect=fake_run):
        outputs = run_demucs(source, tmp_path / "output")

    assert outputs["instrumental"].name == "no_vocals.wav"
    assert outputs["drums"].name == "drums.wav"
