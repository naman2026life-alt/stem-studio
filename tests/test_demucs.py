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


def test_demucs_karaoke_mode_uses_two_stem_output(tmp_path: Path):
    source = tmp_path / "song.wav"
    source.touch()
    stem_dir = tmp_path / "output" / "htdemucs" / "song"

    def fake_run(command, **kwargs):
        stem_dir.mkdir(parents=True, exist_ok=True)
        AudioSegment.silent(duration=100).export(stem_dir / "vocals.wav", format="wav")
        AudioSegment.silent(duration=100).export(stem_dir / "no_vocals.wav", format="wav")
        assert command[command.index("--two-stems") + 1] == "vocals"
        return Mock(returncode=0, stdout="", stderr="")

    with (
        patch("stem_studio.audio.subprocess.run", side_effect=fake_run) as run,
        patch("stem_studio.audio.AudioSegment.from_file") as read_stem,
    ):
        outputs = run_demucs(source, tmp_path / "output", karaoke_only=True)

    assert run.call_count == 1
    read_stem.assert_not_called()
    assert list(outputs) == ["instrumental"]
    assert outputs["instrumental"].name == "no_vocals.wav"


def test_demucs_karaoke_mode_requires_no_vocals_output(tmp_path: Path):
    source = tmp_path / "song.wav"
    source.touch()
    stem_dir = tmp_path / "output" / "htdemucs" / "song"

    def fake_run(command, **kwargs):
        stem_dir.mkdir(parents=True, exist_ok=True)
        AudioSegment.silent(duration=100).export(stem_dir / "vocals.wav", format="wav")
        return Mock(returncode=0, stdout="", stderr="")

    with patch("stem_studio.audio.subprocess.run", side_effect=fake_run):
        try:
            run_demucs(source, tmp_path / "output", karaoke_only=True)
        except RuntimeError as error:
            assert "karaoke output" in str(error)
        else:
            raise AssertionError("Karaoke mode should fail when no_vocals.wav is missing.")
