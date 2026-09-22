# Singing coach: validation and limits

## What is measured

One dominant vocal fundamental, not chords or a singer's overall ability. The
graph uses logarithmic note spacing, retains continuous cents deviations, and
leaves uncertain frames unvoiced. The nominal frame interval is 10 ms, but the
model observes approximately 64 ms of audio: it cannot locate an attack with
10 ms physical precision. Early-note and stability summaries are descriptive
practice aids. They are not medical, professional, or raga assessments.

## Development checks (22 September 2026)

The ten generated-audio cases exercise a missing fundamental, dominant upper
harmonics, noise/silence, real octave changes, vibrato, scooped entries, quiet
singing, and added noise. All passed actual-model checks: zero detected octave
errors, 100% voiced recall on the scored synthetic regions, and a worst-case
95th-percentile pitch error of 4.26 cents. Synthetic signals are deliberately
controlled and are easier than arbitrary phone recordings.

Eight recordings from [Vocadito v3](https://zenodo.org/records/5578807) were also
evaluated against its human-corrected annotations: IDs 1, 2, 4, 6, 10, 17, 35,
and 37, totaling approximately 196 seconds. About **98.9%** of 14,354 annotated
voiced frames were within 50 cents; individual recordings ranged roughly
97.9–99.9%. Voiced recall was 99–100%, but voiced precision was only 83–96%:
some breath/consonant boundaries were mistakenly treated as pitched. These
recordings helped tune harmonic correction, so this is a small development
benchmark, **not an independent held-out accuracy claim**. Some harmonic errors
remain. The dataset is CC BY 4.0; no dataset audio is included in this repository.

To repeat with a separately obtained local dataset:

```bash
source .venv/bin/activate
python scripts/benchmark_pitch.py
python scripts/benchmark_pitch.py --vocadito-dir /path/to/vocadito --real-only
```

## Why smoothing is restrained

The full CREPE model's evidence is decoded over the complete phrase. Strong
evidence can cross a real octave boundary. Independent waveform repetition
supports correction of false second/third harmonics; short corrections need
neighboring-note agreement. A tiny median filter removes isolated jitter.
There is no autotune, note quantization, or automatic octave folding. Genuine
slides and vibrato remain visible, including when they reduce a reference score.

## Comparison safeguards

- Only one global time offset is estimated; there is no time stretching.
- A key/octave adjustment must be selected explicitly and is displayed.
- Missing reference notes reduce the score; silence cannot earn perfect marks.
- Weak extraction evidence, insufficient overlap, and ambiguous alignment are
  unscored. Steady tones, repeated phrases, and continuous linear glides can
  require manual timing.
- Attack feedback distinguishes an entry scoop from singing the entire note
  consistently flat or sharp.
- Tests cover known positive/negative offsets, intentionally wrong notes/octaves,
  sparse data, malformed values, vibrato, missing notes, and weak evidence.

## Operational and device limits

Analyze short phrases: 15 seconds by default and 90 seconds maximum. Solo voices
skip Demucs; accompanied sections use the existing shared single-GPU worker.
CPU extraction on the development Mac took around 0.8–0.9 seconds per second of
audio. Hosted speed also depends on cold starts and other queued work.

The interface is responsive, with touch-sized controls, clip-start wheels,
zoom, graph seeking, playback, and Western or user-selected Sa note labels.
Browser recording requires microphone permission and a secure connection.
Recording stops when hidden or when switching modules; analysis is after
recording, not a live tuner. Phone-size browser checks are not a substitute for
hardware testing every iPhone/Safari version.

Temporary analyses and playback links expire after one hour. Save recordings
you want to keep. Uploaded originals are discarded after clipping; only the
requested section is retained for analysis. No account, database, new cloud
storage service, or extra independently scaling GPU worker was added.
