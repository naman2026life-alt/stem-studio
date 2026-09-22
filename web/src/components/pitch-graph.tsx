"use client";

import { useEffect, useId, useMemo, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, Headphones, ScanLine } from "lucide-react";

import { contourPath, hzToMidi, NOTE_NAMES, noteLabel, pitchDescription, practiceTime, type PitchAnalysis, type PitchComparison } from "@/lib/practice";

export function PitchGraph({ analysis, audioUrl, comparison, referenceAudioUrl, referenceAnalysis }: {
  analysis: PitchAnalysis;
  audioUrl: string;
  comparison: PitchComparison | null;
  referenceAudioUrl?: string;
  referenceAnalysis?: PitchAnalysis | null;
}) {
  const [showRaw, setShowRaw] = useState(false);
  const [tonic, setTonic] = useState<number | null>(null);
  const [span, setSpan] = useState(0);
  const [viewStart, setViewStart] = useState(0);
  const [cursor, setCursor] = useState(0);
  const [listen, setListen] = useState<"take" | "reference">("take");
  const [width, setWidth] = useState(700);
  const [audioError, setAudioError] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const audioRef = useRef<HTMLAudioElement>(null);
  const clipId = useId().replace(/:/g, "");
  const duration = comparison ? referenceAnalysis?.duration_seconds ?? analysis.duration_seconds : analysis.duration_seconds;
  const displaySpan = Math.min(span || duration, duration);
  const start = Math.min(viewStart, Math.max(0, duration - displaySpan));
  const end = start + displaySpan;
  const left = tonic === null ? 45 : 77;
  const right = 15;
  const top = 20;
  const height = 320;
  const bottom = 30;
  const offset = comparison?.alignment.offset_seconds ?? 0;
  const source = listen === "reference" && comparison ? referenceAudioUrl || audioUrl : audioUrl;

  useEffect(() => {
    const node = containerRef.current;
    if (!node) return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(280, entry.contentRect.width)));
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  const takePoints = useMemo(() => comparison
    ? comparison.points.map((point) => ({ time: point.time, midi: hzToMidi(point.take_hz) }))
    : analysis.frames.map((frame) => ({ time: frame.time, midi: frame.midi })), [analysis, comparison]);
  const referencePoints = useMemo(() => comparison?.points.map((point) => ({ time: point.time, midi: hzToMidi(point.reference_hz) })) ?? [], [comparison]);
  const rawPoints = useMemo(() => analysis.frames.map((frame) => ({ time: frame.time, midi: hzToMidi(frame.raw_hz) })), [analysis]);
  const bounds = useMemo(() => {
    const values = [...takePoints, ...referencePoints, ...(showRaw && !comparison ? rawPoints : [])].filter((point) => point.time >= start && point.time <= end && point.midi !== null).map((point) => point.midi as number);
    if (!values.length) return [48, 72];
    const low = Math.floor(Math.min(...values)) - 2;
    const high = Math.ceil(Math.max(...values)) + 2;
    return high - low < 10 ? [low - (10 - high + low) / 2, high + (10 - high + low) / 2] : [low, high];
  }, [takePoints, referencePoints, showRaw, comparison, rawPoints, start, end]);
  const [low, high] = bounds;
  const geometry = useMemo(() => {
    const x = (time: number) => left + (time - start) / Math.max(0.001, end - start) * (width - left - right);
    const y = (midi: number) => top + (high - midi) / (high - low) * (height - top - bottom);
    const gap = Math.max(analysis.hop_seconds * 2.5, 0.065);
    return {
      x, y,
      take: contourPath(takePoints, start, end, x, y, gap),
      reference: contourPath(referencePoints, start, end, x, y, gap),
      raw: showRaw && !comparison ? contourPath(rawPoints, start, end, x, y, gap) : "",
    };
  }, [left, start, end, width, high, low, analysis.hop_seconds, takePoints, referencePoints, showRaw, comparison, rawPoints]);
  const noteStep = high - low > 26 ? 3 : high - low > 16 ? 2 : 1;
  const ticks = Array.from({ length: Math.ceil(high - low) + 1 }, (_, i) => Math.ceil(low) + i).filter((midi) => midi <= high && midi % noteStep === 0);
  const selected = useMemo(() => {
    let nearest = takePoints[0];
    for (const point of takePoints) if (!nearest || Math.abs(point.time - cursor) < Math.abs(nearest.time - cursor)) nearest = point;
    const noteTime = comparison ? cursor - offset : cursor;
    const note = analysis.notes.find((item) => noteTime >= item.start && noteTime <= item.end);
    return { point: nearest, note };
  }, [takePoints, cursor, comparison, offset, analysis.notes]);

  function seek(time: number) {
    const next = Math.max(0, Math.min(duration, time));
    setCursor(next);
    if (audioRef.current) audioRef.current.currentTime = Math.max(0, next - (listen === "take" ? offset : 0));
  }

  function syncCursor() {
    const time = (audioRef.current?.currentTime ?? 0) + (listen === "take" ? offset : 0);
    setCursor(time);
    if (audioRef.current && !audioRef.current.paused && (time > end || time < start)) setViewStart(Math.max(0, Math.min(time, duration - displaySpan)));
  }

  return (
    <div className="pitch-chart" ref={containerRef}>
      <div className="pitch-chart-toolbar">
        <div className="pitch-legend"><span><i />Your voice</span>{comparison && <span className="is-reference"><i />Reference</span>}</div>
        <div className="pitch-chart-options">
          {!comparison && <label className="coach-check"><input checked={showRaw} onChange={(event) => setShowRaw(event.target.checked)} type="checkbox" />Raw estimates</label>}
          <label className="coach-select-label">Labels<select aria-label="Pitch note labels and Sa tonic" value={tonic ?? "western"} onChange={(event) => setTonic(event.target.value === "western" ? null : Number(event.target.value))}><option value="western">A, B, C notes</option>{NOTE_NAMES.map((name, index) => <option key={name} value={index}>Sa = {name}</option>)}</select></label>
        </div>
      </div>
      <svg
        aria-label={`Pitch over time. ${comparison ? "Your voice in violet, reference in mint." : "Your clean pitch in violet."} Use the time slider below or tap the graph to inspect a moment.`}
        className="pitch-svg"
        onClick={(event) => { const rect = event.currentTarget.getBoundingClientRect(); seek(start + (event.clientX - rect.left - left) / (width - left - right) * displaySpan); }}
        role="img"
        viewBox={`0 0 ${width} ${height}`}
      >
        <defs><clipPath id={clipId}><rect x={left} y={top} width={Math.max(1, width - left - right)} height={height - top - bottom} /></clipPath></defs>
        {ticks.map((midi) => <g key={midi}><line className={midi % 12 === 0 ? "pitch-grid is-octave" : "pitch-grid"} x1={left} y1={geometry.y(midi)} x2={width - right} y2={geometry.y(midi)} /><text className="pitch-label" textAnchor="end" x={left - 8} y={geometry.y(midi) + 4}>{noteLabel(midi, tonic)}</text></g>)}
        {Array.from({ length: width < 450 ? 5 : 7 }, (_, index) => index).map((_, index, values) => {
          const time = start + displaySpan * index / (values.length - 1);
          return <g key={index}><line className="pitch-time-grid" x1={geometry.x(time)} x2={geometry.x(time)} y1={top} y2={height - bottom} /><text className="pitch-label" x={geometry.x(time)} y={height - 8} textAnchor="middle">{time.toFixed(displaySpan <= 5 ? 1 : 0)}s</text></g>;
        })}
        <g clipPath={`url(#${clipId})`}>
          {geometry.raw && <path className="pitch-trace is-raw" d={geometry.raw} />}
          {geometry.reference && <path className="pitch-trace is-reference" d={geometry.reference} />}
          {geometry.take && <path className="pitch-trace" d={geometry.take} />}
          {cursor >= start && cursor <= end && <line className="pitch-cursor" x1={geometry.x(cursor)} x2={geometry.x(cursor)} y1={top} y2={height - bottom} />}
          {selected.point?.midi !== null && selected.point?.midi !== undefined && cursor >= start && cursor <= end && <circle className="pitch-cursor-dot" cx={geometry.x(cursor)} cy={geometry.y(selected.point.midi)} r={5} />}
        </g>
        {!geometry.take && <text className="pitch-empty-label" x={(width + left) / 2} y={height / 2} textAnchor="middle">No confident vocal pitch in this window</text>}
      </svg>
      <div className="pitch-inspector" aria-live="off">
        <span className="pitch-inspector-time"><ScanLine size={15} />{practiceTime(cursor)}</span>
        <strong>{selected.point?.midi != null ? noteLabel(selected.point.midi, tonic) : "Unvoiced"}</strong>
        <span>{selected.point?.midi != null ? `${Math.round(440 * 2 ** ((selected.point.midi - 69) / 12))} Hz · ${pitchDescription(selected.point.midi)}` : "Breath, silence, or uncertain pitch"}</span>
      </div>
      <input aria-label="Inspect pitch at time" aria-valuetext={`${practiceTime(cursor)} ${selected.point?.midi != null ? noteLabel(selected.point.midi, tonic) : "unvoiced"}`} className="pitch-scrubber" min={0} max={duration} onChange={(event) => seek(Number(event.target.value))} step={analysis.hop_seconds} type="range" value={Math.max(0, Math.min(duration, cursor))} />
      <div className="pitch-navigation">
        <label className="coach-select-label">View<select aria-label="Pitch graph zoom" value={span} onChange={(event) => { setSpan(Number(event.target.value)); setViewStart(Math.max(0, cursor - Number(event.target.value) / 2)); }}><option value={0}>Full phrase</option>{[10, 5, 2].filter((value) => value < duration).map((value) => <option key={value} value={value}>{value} seconds</option>)}</select></label>
        <div className="pitch-window-buttons"><button aria-label="Previous time window" disabled={start <= 0} onClick={() => setViewStart(Math.max(0, start - displaySpan / 2))} type="button"><ChevronLeft size={19} /></button><span>{practiceTime(start)}–{practiceTime(end)}</span><button aria-label="Next time window" disabled={end >= duration} onClick={() => setViewStart(Math.min(duration - displaySpan, start + displaySpan / 2))} type="button"><ChevronRight size={19} /></button></div>
      </div>
      <div className="pitch-audio-player">
        {comparison && referenceAudioUrl ? <div className="coach-segmented" role="group" aria-label="Listen to"><button aria-pressed={listen === "take"} onClick={() => { audioRef.current?.pause(); setListen("take"); setAudioError(false); }} type="button">Your voice</button><button aria-pressed={listen === "reference"} onClick={() => { audioRef.current?.pause(); setListen("reference"); setAudioError(false); }} type="button">Reference</button></div> : <span><Headphones size={15} />Listen and follow your pitch</span>}
        <audio aria-label={listen === "reference" ? "Reference phrase playback" : "Your analysed phrase playback"} controls key={source} onError={() => setAudioError(true)} onLoadedMetadata={() => { if (audioRef.current) audioRef.current.currentTime = Math.max(0, cursor - (listen === "take" ? offset : 0)); }} onTimeUpdate={syncCursor} preload="metadata" ref={audioRef} src={source} />
        {audioError && <p className="coach-error">This temporary audio is unavailable. Analyse the source again to restore playback.</p>}
      </div>
      <p className="coach-fineprint">Tap the graph to listen from a moment. Gaps are unvoiced or uncertain—not wrong notes. Vibrato and slides stay visible; the line is not snapped to notes.{tonic !== null ? ` Sa is set to ${NOTE_NAMES[tonic]} by you. Lowercase re, ga, dha, ni = komal; Ma♯ = tivra.` : " A4 = 440 Hz."}</p>
      {comparison && <p className="coach-fineprint">Graph times follow the reference phrase. The audio player shows time within the clip you’re listening to.</p>}
      {selected.note && !comparison && <div className="pitch-note-detail"><strong>{noteLabel(selected.note.midi, tonic)} · {practiceTime(selected.note.start)}–{practiceTime(selected.note.end)}</strong><span>Steadiness: {Math.round(selected.note.stability_cents)}¢ spread{selected.note.attack_cents !== null ? ` · Entry ${Math.abs(Math.round(selected.note.attack_cents))}¢ ${selected.note.attack_cents < 0 ? "below" : "above"} its sustained pitch` : " · Entry too brief to assess"}</span><small>Entry is relative to this note’s own sustained pitch. A scooped entry or vibrato can be intentional.</small></div>}
    </div>
  );
}
