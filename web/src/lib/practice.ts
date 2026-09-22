export type PitchFrame = { time: number; hz: number | null; raw_hz: number | null; midi: number | null; confidence: number };
export type PitchNote = { start: number; end: number; midi: number; note: string; median_hz: number; stability_cents: number; attack_cents: number | null };
export type PitchAnalysis = {
  duration_seconds: number;
  hop_seconds: number;
  frames: PitchFrame[];
  notes: PitchNote[];
  summary: { voiced_seconds: number; voiced_percent: number; median_confidence: number; lowest_note: string | null; highest_note: string | null; note_count: number };
  warnings: string[];
  engine: string;
};
export type PracticeJob = {
  id: string;
  status: "queued" | "processing" | "completed" | "failed";
  stage: string;
  progress: number;
  created_at: string;
  expires_in_seconds: number;
  source_name: string;
  source_start_seconds?: number;
  error: string | null;
  result: PitchAnalysis | null;
  audio_url: string | null;
};
export type PitchComparison = {
  score: number | null;
  reliable: boolean;
  unscored_reason: string | null;
  alignment: { mode: "auto" | "manual"; offset_seconds: number; confidence: "high" | "medium" | "low"; ambiguous: boolean };
  transpose_semitones: number;
  metrics: { median_absolute_cents: number | null; bias_cents: number | null; within_50_cents_percent: number | null; reference_coverage_percent: number; overlap_seconds: number; matched_voiced_seconds: number };
  points: { time: number; reference_hz: number | null; take_hz: number | null; cents_error: number | null }[];
  note_feedback: { start: number; end: number; note: string; median_error_cents: number | null; attack_error_cents: number | null; coverage_percent: number }[];
  feedback: { kind: "strength" | "practice" | "info"; title: string; detail: string }[];
};

export const NOTE_NAMES = ["C", "C♯", "D", "D♯", "E", "F", "F♯", "G", "G♯", "A", "A♯", "B"];
const SWARAS = ["Sa", "re", "Re", "ga", "Ga", "Ma", "Ma♯", "Pa", "dha", "Dha", "ni", "Ni"];

export function hzToMidi(hz: number | null): number | null {
  return hz !== null && hz > 0 && Number.isFinite(hz) ? 69 + 12 * Math.log2(hz / 440) : null;
}

export function noteLabel(midi: number, tonic: number | null = null) {
  const rounded = Math.round(midi);
  const pitchClass = ((rounded % 12) + 12) % 12;
  const western = `${NOTE_NAMES[pitchClass]}${Math.floor(rounded / 12) - 1}`;
  return tonic === null ? western : `${SWARAS[(pitchClass - tonic + 12) % 12]} · ${western}`;
}

export function pitchDescription(midi: number) {
  const cents = Math.round((midi - Math.round(midi)) * 100);
  return Math.abs(cents) <= 4 ? "centered" : `${Math.abs(cents)}¢ ${cents > 0 ? "sharp" : "flat"}`;
}

export function practiceTime(seconds: number) {
  const total = Math.max(0, seconds);
  return `${Math.floor(total / 60)}:${(total % 60).toFixed(1).padStart(4, "0")}`;
}

export function parsePracticeTime(value: string): number {
  if (!/^\s*\d+(?:\.\d+)?(?:\:\d+(?:\.\d+)?){0,2}\s*$/.test(value)) return NaN;
  return value.trim().split(":").map(Number).reduce((seconds, part) => seconds * 60 + part, 0);
}

export function contourPath(
  points: { time: number; midi: number | null }[],
  start: number,
  end: number,
  x: (time: number) => number,
  y: (midi: number) => number,
  maxGap: number,
) {
  let path = "";
  let previousTime: number | null = null;
  for (const point of points) {
    if (point.time < start || point.time > end) { previousTime = null; continue; }
    if (point.midi === null || !Number.isFinite(point.midi)) { previousTime = null; continue; }
    const command = previousTime === null || point.time - previousTime > maxGap ? "M" : "L";
    path += `${command}${x(point.time).toFixed(2)},${y(point.midi).toFixed(2)} `;
    previousTime = point.time;
  }
  return path;
}

export function safePracticeId(value: unknown): value is string {
  return typeof value === "string" && /^[a-zA-Z0-9_-]{8,80}$/.test(value);
}
