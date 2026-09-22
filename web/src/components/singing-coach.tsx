"use client";

import { ChangeEvent, useCallback, useEffect, useRef, useState } from "react";
import { Activity, ArrowDownUp, ArrowRight, Check, ChevronDown, CircleStop, FileAudio, Headphones, LoaderCircle, Mic2, Music2, RotateCcw, Sparkles, Timer, Upload, X } from "lucide-react";

import { ExportActions } from "@/components/export-actions";
import { DurationPicker } from "@/components/duration-picker";
import { PitchGraph } from "@/components/pitch-graph";
import { noteLabel, parsePracticeTime, practiceTime, safePracticeId, type PitchComparison, type PracticeJob } from "@/lib/practice";
import { isMissingProcessorResource, processingErrorMessage, type ProcessorLocation } from "@/lib/processor-connection";
import type { SeparationJob } from "@/lib/types";

type Role = "take" | "reference";
type Source = { file: File | null; selection: string; kind: "isolated" | "mixed"; start: string; duration: number };
type RecorderState = "idle" | "requesting" | "recording";
const STORAGE_KEY = "stem-studio-practice-v1";
const ACCEPT = ".mp3,.wav,.m4a,.flac,.aac,.ogg,.webm,.mp4,.mov,audio/*,video/mp4,video/quicktime";
const INITIAL_SOURCE: Source = { file: null, selection: "upload", kind: "isolated", start: "0:00", duration: 15 };

function absoluteUrl(base: string, path: string | null) {
  return path ? new URL(path, `${base}/`).toString() : "";
}

async function responseError(response: Response) {
  const body = await response.json().catch(() => ({})) as { detail?: unknown; error?: string };
  return typeof body.detail === "string" ? body.detail : body.error || `Could not finish this request (${response.status}). Please try again.`;
}

function saveSession(take: string | null, reference: string | null) {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ take, reference })); } catch { /* Private tabs can disable storage. */ }
}

function useFileUrl(file: File | null) {
  const [resource, setResource] = useState<{ file: File; url: string } | null>(null);
  useEffect(() => {
    if (!file) return;
    const next = URL.createObjectURL(file);
    const timer = window.setTimeout(() => setResource({ file, url: next }), 0);
    return () => { window.clearTimeout(timer); URL.revokeObjectURL(next); };
  }, [file]);
  return resource?.file === file ? resource?.url ?? "" : "";
}

function JobProgress({ job, uploading }: { job: PracticeJob | null; uploading: boolean }) {
  if (!uploading && (!job || job.status === "completed" || job.status === "failed")) return null;
  return <div className="coach-job-progress" role="status"><div><LoaderCircle size={17} className="animate-spin" /><strong>{uploading ? "Uploading your phrase…" : job?.stage || "Preparing your phrase…"}</strong><span>{uploading ? "" : `${Math.round(job?.progress ?? 0)}%`}</span></div><div className="progress-track" role="progressbar" aria-label="Vocal pitch analysis progress" aria-valuemin={0} aria-valuemax={100} aria-valuenow={uploading ? undefined : job?.progress}><span style={{ width: `${uploading ? 6 : Math.max(6, job?.progress ?? 0)}%` }} /></div><p>You can leave this tab and return. Processing continues; temporary results reconnect automatically.</p></div>;
}

function SourceControls({ role, source, setSource, activeAudio, vocals, disabled, onError }: {
  role: Role;
  source: Source;
  setSource: (source: Source) => void;
  activeAudio: File | null;
  vocals: SeparationJob[];
  disabled: boolean;
  onError: (error: string) => void;
}) {
  const isReference = role === "reference";
  const [pickerOpen, setPickerOpen] = useState(false);
  const pickerTriggerRef = useRef<HTMLButtonElement>(null);
  const closePicker = () => { setPickerOpen(false); pickerTriggerRef.current?.focus(); };
  const selectFile = (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if (file.size > 150 * 1024 * 1024) { onError("Choose a file smaller than 150 MB."); return; }
    setSource({ ...source, file, selection: "upload", start: "0:00" });
    onError("");
  };
  const name = source.selection === "active" ? activeAudio?.name : source.selection === "upload" ? source.file?.name : vocals.find((job) => job.id === source.selection)?.source_name;

  return <div className="coach-source-controls">
    <div className="coach-source-row">
      <label className="coach-file-picker"><Upload size={18} /><span>{source.file && source.selection === "upload" ? source.file.name : isReference ? "Upload reference song or vocal" : "Upload your singing"}</span><input accept={ACCEPT} aria-label={isReference ? "Upload reference audio" : "Upload your singing audio"} disabled={disabled} onChange={selectFile} onClick={(event) => { event.currentTarget.value = ""; }} type="file" /></label>
      {(activeAudio || vocals.length > 0) && <label className="coach-select-label coach-existing-label">Or use studio audio<select aria-label={`${isReference ? "Reference" : "Your voice"} audio source`} disabled={disabled} onChange={(event) => setSource({ ...source, selection: event.target.value, kind: event.target.value !== "active" && event.target.value !== "upload" ? "isolated" : source.kind, start: "0:00" })} value={source.selection}><option value="upload">Uploaded file</option>{activeAudio && <option value="active">Active audio · {activeAudio.name}</option>}{vocals.map((job) => <option key={job.id} value={job.id}>Isolated vocal · {job.source_name}</option>)}</select></label>}
    </div>
    {name && <p className="coach-selected-source"><FileAudio size={14} /><span>{name}</span></p>}
    <div className="coach-clip-fields">
      <label>Clip starts at<span className="coach-start-input"><input aria-label={`${isReference ? "Reference" : "Your voice"} clip start`} autoComplete="off" disabled={disabled} inputMode="decimal" onChange={(event) => setSource({ ...source, start: event.target.value })} placeholder="0:00" type="text" value={source.start} /><button aria-label={`Choose ${isReference ? "reference" : "your voice"} clip start with scroll wheels`} disabled={disabled} onClick={() => setPickerOpen(true)} ref={pickerTriggerRef} type="button"><Timer size={17} /></button></span><small>Seconds or MM:SS in the source</small></label>
      <label>Phrase length<select aria-label={`${isReference ? "Reference" : "Your voice"} phrase length`} disabled={disabled} onChange={(event) => setSource({ ...source, duration: Number(event.target.value) })} value={source.duration}>{[10, 15, 30, 60, 90].map((seconds) => <option key={seconds} value={seconds}>{seconds} seconds</option>)}</select><small>Up to 90 seconds per analysis</small></label>
    </div>
    {(source.selection === "upload" || source.selection === "active") && <div className="coach-input-kind"><span>What’s in this audio?</span><div className="coach-segmented" role="group" aria-label={`${isReference ? "Reference" : "Your voice"} recording content`}><button aria-pressed={source.kind === "isolated"} disabled={disabled} onClick={() => setSource({ ...source, kind: "isolated" })} type="button"><Mic2 size={14} />Voice only</button><button aria-pressed={source.kind === "mixed"} disabled={disabled} onClick={() => setSource({ ...source, kind: "mixed" })} type="button"><Music2 size={14} />Song with music</button></div><p>{source.kind === "mixed" ? "We isolate vocals from just this phrase first. This adds some processing time." : "Best for one singer with no backing music. Breaths and silent moments are left as gaps."}</p></div>}
    {pickerOpen && <DurationPicker allowTrackEnd={false} initialSeconds={Number.isFinite(parsePracticeTime(source.start)) ? parsePracticeTime(source.start) : 0} label={`${isReference ? "Reference" : "Your voice"} · Clip start`} maxSeconds={0} onCancel={closePicker} onConfirm={(seconds) => { setSource({ ...source, start: practiceTime(seconds) }); closePicker(); }} onUseTrackEnd={closePicker} />}
  </div>;
}

export function SingingCoach({ active, activeAudio, authorize, jobs, processorLocation, processorUrl, requestedReference }: {
  active: boolean;
  activeAudio: File | null;
  authorize: () => Promise<Record<string, string>>;
  jobs: SeparationJob[];
  processorLocation: ProcessorLocation;
  processorUrl: string;
  requestedReference: string | null;
}) {
  const [takeSource, setTakeSource] = useState<Source>(INITIAL_SOURCE);
  const [referenceSource, setReferenceSource] = useState<Source>({ ...INITIAL_SOURCE, kind: "mixed" });
  const [takeJob, setTakeJob] = useState<PracticeJob | null>(null);
  const [referenceJob, setReferenceJob] = useState<PracticeJob | null>(null);
  const [uploading, setUploading] = useState<Role | null>(null);
  const [error, setError] = useState("");
  const [networkHint, setNetworkHint] = useState("");
  const [recorderState, setRecorderState] = useState<RecorderState>("idle");
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [micLevel, setMicLevel] = useState(0);
  const [recordedFile, setRecordedFile] = useState<File | null>(null);
  const [referenceOpen, setReferenceOpen] = useState(false);
  const [compareBusy, setCompareBusy] = useState(false);
  const [comparison, setComparison] = useState<PitchComparison | null>(null);
  const [showComparison, setShowComparison] = useState(true);
  const [alignment, setAlignment] = useState<"auto" | "manual">("auto");
  const [offset, setOffset] = useState("0");
  const [transpose, setTranspose] = useState(0);
  const [comparisonDirty, setComparisonDirty] = useState(false);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const animationRef = useRef<number>(0);
  const mountedRef = useRef(true);
  const requestGenerationRef = useRef(0);
  const requestAbortRef = useRef<AbortController | null>(null);
  const pollRef = useRef<{ take: string | null; reference: string | null }>({ take: null, reference: null });
  const nextPollRef = useRef<Record<string, number>>({});
  const resultsRef = useRef<HTMLElement>(null);
  const recordedUrl = useFileUrl(recordedFile);
  const vocals = jobs.filter((job) => job.status === "completed" && job.expires_in_seconds > 0 && job.vocals_url);
  const processing = uploading !== null || compareBusy;
  const recording = recorderState !== "idle";
  const takePending = takeJob?.status === "queued" || takeJob?.status === "processing";
  const referencePending = referenceJob?.status === "queued" || referenceJob?.status === "processing";
  const analysis = takeJob?.status === "completed" ? takeJob.result : null;
  const referenceAnalysis = referenceJob?.status === "completed" ? referenceJob.result : null;
  const visibleComparison = showComparison && !comparisonDirty ? comparison : null;

  const releaseMicrophone = useCallback(() => {
    cancelAnimationFrame(animationRef.current);
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    void audioContextRef.current?.close();
    audioContextRef.current = null;
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      requestGenerationRef.current += 1;
      requestAbortRef.current?.abort();
      const recorder = recorderRef.current;
      if (recorder && recorder.state !== "inactive") { recorder.onstop = null; recorder.stop(); }
      releaseMicrophone();
    };
  }, [releaseMicrophone]);

  useEffect(() => {
    if (active) return;
    requestGenerationRef.current += 1;
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
    releaseMicrophone();
  }, [active, releaseMicrophone]);

  useEffect(() => {
    const stopWhenHidden = () => {
      if (!document.hidden) return;
      if (recorderRef.current?.state === "recording") recorderRef.current.stop();
      requestGenerationRef.current += 1;
    };
    document.addEventListener("visibilitychange", stopWhenHidden);
    return () => document.removeEventListener("visibilitychange", stopWhenHidden);
  }, []);

  useEffect(() => {
    if (!requestedReference) return;
    const timer = window.setTimeout(() => {
      setReferenceSource((source) => ({ ...source, selection: requestedReference, kind: "isolated", start: "0:00" }));
      setReferenceOpen(true);
    }, 0);
    return () => window.clearTimeout(timer);
  }, [requestedReference]);

  useEffect(() => {
    let stopped = false;
    let timer = 0;
    let inFlight = false;
    const abort = new AbortController();
    try {
      const stored = JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}") as { take?: unknown; reference?: unknown };
      pollRef.current = { take: safePracticeId(stored.take) ? stored.take : null, reference: safePracticeId(stored.reference) ? stored.reference : null };
    } catch { /* Fresh session if storage is unavailable. */ }
    const poll = async () => {
      if (!processorUrl || inFlight || stopped) return;
      inFlight = true;
      let offline = false;
      await Promise.all((["take", "reference"] as Role[]).map(async (role) => {
        const id = pollRef.current[role];
        if (!id) return;
        if ((nextPollRef.current[id] ?? 0) > Date.now()) return;
        try {
          const response = await fetch(`${processorUrl}/practice/jobs/${id}`, { cache: "no-store", signal: abort.signal });
          if (stopped || pollRef.current[role] !== id) return;
          const setter = role === "take" ? setTakeJob : setReferenceJob;
          if (await isMissingProcessorResource(response)) {
            pollRef.current[role] = null;
            saveSession(pollRef.current.take, pollRef.current.reference);
            setter(null);
            setComparison(null);
            setNetworkHint("A previous phrase has expired. Choose its audio and analyse it again.");
            return;
          }
          if (!response.ok) { offline = true; return; }
          const job = await response.json() as PracticeJob;
          if (stopped || pollRef.current[role] !== id) return;
          setter(job);
          nextPollRef.current[id] = Date.now() + (job.status === "completed" || job.status === "failed" ? 60_000 : 2_000);
          if (role === "reference") setReferenceOpen(true);
        } catch (error) { if (!(error instanceof DOMException && error.name === "AbortError")) offline = true; }
      }));
      inFlight = false;
      if (stopped) return;
      if (offline) setNetworkHint(processorLocation === "mac" ? "Connection interrupted. Keep your Mac awake and online. We’ll reconnect to this analysis automatically—no need to upload again." : "Connection interrupted. We’re reconnecting to your analysis automatically.");
      else setNetworkHint((hint) => hint.startsWith("Connection interrupted") ? "" : hint);
      timer = window.setTimeout(poll, document.hidden ? 10_000 : 3_000);
    };
    timer = window.setTimeout(poll, 0);
    const onVisible = () => { if (!document.hidden && !inFlight) { window.clearTimeout(timer); nextPollRef.current = {}; void poll(); } };
    document.addEventListener("visibilitychange", onVisible);
    return () => { stopped = true; abort.abort(); window.clearTimeout(timer); document.removeEventListener("visibilitychange", onVisible); };
  }, [processorLocation, processorUrl]);

  async function startRecording() {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") { setError("Recording needs a supported browser with microphone permission. You can also upload an audio file."); return; }
    setError("");
    setRecorderState("requesting");
    const generation = ++requestGenerationRef.current;
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false } });
      if (!mountedRef.current || generation !== requestGenerationRef.current) { stream.getTracks().forEach((track) => track.stop()); if (mountedRef.current) setRecorderState("idle"); return; }
      streamRef.current = stream;
      const mimeType = ["audio/mp4", "audio/webm;codecs=opus", "audio/webm"].find((mime) => MediaRecorder.isTypeSupported(mime));
      let recorder: MediaRecorder;
      try { recorder = new MediaRecorder(stream, { ...(mimeType ? { mimeType } : {}), audioBitsPerSecond: 192000 }); } catch { recorder = new MediaRecorder(stream); }
      const chunks: Blob[] = [];
      recorderRef.current = recorder;
      recorder.ondataavailable = (event) => { if (event.data.size) chunks.push(event.data); };
      recorder.onerror = () => { releaseMicrophone(); if (mountedRef.current) { setRecorderState("idle"); setError("The microphone stopped unexpectedly. Please record a new take."); } };
      recorder.onstop = () => {
        releaseMicrophone();
        recorderRef.current = null;
        if (!mountedRef.current) return;
        setRecorderState("idle");
        setMicLevel(0);
        if (!chunks.length) { setError("No audio was captured. Check microphone permission and try again."); return; }
        const type = recorder.mimeType || chunks[0].type || "audio/webm";
        const file = new File(chunks, `my-singing-${new Date().toISOString().replace(/[:.]/g, "-")}.${type.includes("mp4") ? "m4a" : "webm"}`, { type });
        setRecordedFile(file);
        setTakeSource((source) => ({ ...source, file, selection: "upload", start: "0:00", kind: "isolated" }));
      };
      recorder.start(250);
      setRecordingSeconds(0);
      setRecorderState("recording");
      const started = performance.now();
      let analyser: AnalyserNode | null = null;
      let samples: Uint8Array<ArrayBuffer> | null = null;
      try {
        const context = new AudioContext();
        audioContextRef.current = context;
        void context.resume().catch(() => {});
        analyser = context.createAnalyser();
        analyser.fftSize = 256;
        context.createMediaStreamSource(stream).connect(analyser);
        samples = new Uint8Array(analyser.fftSize);
      } catch { /* Recording still works without a visual meter. */ }
      let lastUpdate = 0;
      const tick = () => {
        const elapsed = (performance.now() - started) / 1000;
        if (recorder.state !== "recording") return;
        if (elapsed >= takeSource.duration) { recorder.stop(); return; }
        if (elapsed - lastUpdate > 0.08) {
          setRecordingSeconds(elapsed);
          if (analyser && samples) { analyser.getByteTimeDomainData(samples); const energy = Math.sqrt(samples.reduce((sum, value) => sum + ((value - 128) / 128) ** 2, 0) / samples.length); setMicLevel(Math.min(1, energy * 6)); }
          lastUpdate = elapsed;
        }
        animationRef.current = requestAnimationFrame(tick);
      };
      animationRef.current = requestAnimationFrame(tick);
    } catch (error) {
      releaseMicrophone();
      setRecorderState("idle");
      setError(error instanceof DOMException && error.name === "NotAllowedError" ? "Microphone access was denied. Allow the microphone in Safari or Chrome site settings, or upload your recording." : "Could not open the microphone. Close other recording apps and try again.");
    }
  }

  async function analyse(role: Role) {
    const source = role === "take" ? takeSource : referenceSource;
    const file = source.selection === "active" ? activeAudio : source.file;
    const usesJob = source.selection !== "upload" && source.selection !== "active";
    const start = parsePracticeTime(source.start);
    if (!Number.isFinite(start) || start < 0) { setError("Enter a valid clip start in seconds or MM:SS, such as 75 or 1:15."); return; }
    if (!usesJob && !file) { setError(role === "take" ? "Record or choose your singing first." : "Choose a reference song or isolated vocal first."); return; }
    setError("");
    setNetworkHint("");
    setUploading(role);
    const controller = new AbortController();
    requestAbortRef.current = controller;
    try {
      const headers = await authorize();
      if (!mountedRef.current) return;
      const body = new FormData();
      if (usesJob) body.append("source_job_id", source.selection);
      else body.append("file", file!);
      body.append("input_kind", usesJob ? "isolated" : source.kind);
      body.append("start_seconds", String(start));
      body.append("duration_seconds", String(source.duration));
      const response = await fetch(`${processorUrl}/practice/jobs`, { method: "POST", headers, body, signal: controller.signal });
      if (!response.ok) throw new Error(await responseError(response));
      const job = await response.json() as PracticeJob;
      pollRef.current[role] = job.id;
      saveSession(pollRef.current.take, pollRef.current.reference);
      (role === "take" ? setTakeJob : setReferenceJob)(job);
      setComparison(null);
      setComparisonDirty(false);
      if (role === "take") window.setTimeout(() => resultsRef.current?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" }), 50);
    } catch (error) { if (!(error instanceof DOMException && error.name === "AbortError")) setError(processingErrorMessage(error, "The analysis could not start. Please try again.", processorLocation)); }
    finally { if (mountedRef.current) setUploading(null); requestAbortRef.current = null; }
  }

  async function compare() {
    if (!takeJob || !referenceJob) return;
    const manualOffset = Number(offset);
    if (!Number.isFinite(manualOffset) || Math.abs(manualOffset) > 90) { setError("Enter an alignment shift between −90 and 90 seconds."); return; }
    setCompareBusy(true);
    setError("");
    const controller = new AbortController();
    requestAbortRef.current = controller;
    try {
      const headers = await authorize();
      const response = await fetch(`${processorUrl}/practice/compare`, { method: "POST", headers: { ...headers, "Content-Type": "application/json" }, body: JSON.stringify({ take_job_id: takeJob.id, reference_job_id: referenceJob.id, alignment, offset_seconds: manualOffset, transpose_semitones: transpose }), signal: controller.signal });
      if (!response.ok) throw new Error(await responseError(response));
      const next = await response.json() as PitchComparison;
      setComparison(next);
      setShowComparison(true);
      setComparisonDirty(false);
      setOffset(String(next.alignment.offset_seconds));
      resultsRef.current?.scrollIntoView({ behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
    } catch (error) { if (!(error instanceof DOMException && error.name === "AbortError")) setError(processingErrorMessage(error, "Could not compare these phrases.", processorLocation)); }
    finally { if (mountedRef.current) setCompareBusy(false); requestAbortRef.current = null; }
  }

  function clearPractice() {
    pollRef.current = { take: null, reference: null };
    saveSession(null, null);
    setTakeJob(null);
    setReferenceJob(null);
    setComparison(null);
    setNetworkHint("");
    setError("");
  }

  return <div className="singing-coach" hidden={!active}>
    <div className="coach-intro"><div><p className="eyebrow">Hear it. See it. Refine it.</p><h2>Get to know your voice.</h2><p>See the notes you actually sing, with the detail that helps you improve. Start with one short phrase.</p></div><span className="coach-intro-mark"><Activity size={32} /></span></div>
    <div className="coach-layout">
      <div className="coach-input-column">
        <section className="coach-card" aria-labelledby="coach-take-heading">
          <div className="coach-card-heading"><span className="coach-step">01</span><div><h3 id="coach-take-heading">Your voice</h3><p>Sing a scale or a line you want to work on.</p></div></div>
          <div className={`coach-recorder ${recorderState === "recording" ? "is-recording" : ""}`}>
            <div className="coach-recording-top"><span className="coach-mic-icon"><Mic2 size={23} /></span><div><strong>{recorderState === "recording" ? `${recordingSeconds.toFixed(1)} / ${takeSource.duration}s` : recorderState === "requesting" ? "Allow microphone access" : "A little practice, right here"}</strong><p>{recorderState === "recording" ? "Sing naturally. We’ll stop at the phrase length." : "One singer. Quiet room. No backing track."}</p></div></div>
            <div className="coach-mic-meter" aria-hidden="true">{Array.from({ length: 25 }, (_, index) => <i key={index} style={{ height: `${Math.round(8 + Math.sin(index * 0.8) ** 2 * 17)}px`, opacity: recorderState === "recording" ? (index / 25 < micLevel ? 1 : 0.16) : 0.18 }} />)}</div>
            {recorderState === "recording" ? <button className="button-primary" onClick={() => recorderRef.current?.stop()} type="button"><CircleStop size={17} />Stop & keep take</button> : <button className="button-primary" disabled={processing || takePending || recorderState === "requesting"} onClick={() => void startRecording()} type="button">{recorderState === "requesting" ? <LoaderCircle className="animate-spin" size={17} /> : <Mic2 size={17} />}{recordedFile ? "Record a new take" : `Record ${takeSource.duration} seconds`}</button>}
            {recorderState === "recording" && <p className="coach-fineprint">Keep this screen open. Leaving it stops and saves your take.</p>}
          </div>
          {recordedFile && recordedUrl && <div className="coach-recorded-preview"><audio aria-label="Your new recording preview" controls preload="metadata" src={recordedUrl} /><ExportActions compact file={recordedFile} /></div>}
          <div className="coach-or"><span />or bring a recording<span /></div>
          <SourceControls role="take" source={takeSource} setSource={setTakeSource} activeAudio={activeAudio} vocals={vocals} disabled={processing || recording || Boolean(takePending)} onError={setError} />
          <button className="button-primary coach-analyse" disabled={!processorUrl || processing || recording || takePending || !(takeSource.selection === "active" ? activeAudio : takeSource.selection !== "upload" || takeSource.file)} onClick={() => void analyse("take")} type="button">{uploading === "take" || takePending ? <LoaderCircle className="animate-spin" size={18} /> : <Activity size={18} />}{takeJob ? "Analyse this take" : "Show my pitch"}<ArrowRight size={17} /></button>
          <JobProgress job={takeJob} uploading={uploading === "take"} />
          {takeJob?.status === "failed" && <p className="coach-error" role="alert">{takeJob.error || "The phrase could not be analysed. Try a clear solo vocal."}</p>}
        </section>

        <section className={`coach-card coach-reference ${referenceOpen ? "is-open" : ""}`} aria-labelledby="coach-reference-heading">
          <button className="coach-reference-toggle" aria-expanded={referenceOpen} onClick={() => setReferenceOpen(!referenceOpen)} type="button"><span className="coach-step">02</span><span><strong id="coach-reference-heading">Compare with a reference</strong><small>Optional · same phrase, two voices</small></span><ChevronDown size={19} /></button>
          {referenceOpen && <div className="coach-reference-body"><p className="coach-paragraph">Choose the song you’re practising, or its isolated vocal. Select exactly the phrase you sang; we’ll line up their starts without stretching time.</p><SourceControls role="reference" source={referenceSource} setSource={setReferenceSource} activeAudio={activeAudio} vocals={vocals} disabled={processing || recording || Boolean(referencePending)} onError={setError} /><button className="button-ghost coach-analyse" disabled={!processorUrl || processing || recording || referencePending || !(referenceSource.selection === "active" ? activeAudio : referenceSource.selection !== "upload" || referenceSource.file)} onClick={() => void analyse("reference")} type="button">{uploading === "reference" || referencePending ? <LoaderCircle className="animate-spin" size={17} /> : <Music2 size={17} />}{referenceJob ? "Analyse reference again" : "Analyse reference phrase"}</button><JobProgress job={referenceJob} uploading={uploading === "reference"} />{referenceJob?.status === "failed" && <p className="coach-error" role="alert">{referenceJob.error}</p>}{referenceAnalysis && <p className="coach-reference-ready"><Check size={16} />Reference ready · {practiceTime(referenceAnalysis.duration_seconds)} · {referenceAnalysis.summary.note_count} notes</p>}
          {referenceAnalysis && referenceJob?.audio_url && <div className="coach-recorded-preview"><audio aria-label="Listen to reference phrase before singing" controls preload="none" src={absoluteUrl(processorUrl, referenceJob.audio_url)} /><p className="coach-fineprint">Listen, then sing the same phrase. Use headphones if you record while it plays. Reference clip begins at {practiceTime(referenceJob.source_start_seconds ?? 0)} in the source.</p></div>}
          {referenceAnalysis?.warnings.map((warning) => <p className="coach-warning" key={warning}>{warning}</p>)}
          {analysis && referenceAnalysis && <div className="coach-comparison-controls"><h4><ArrowDownUp size={16} />Line up the phrases</h4><div className="coach-segmented" role="group" aria-label="Comparison alignment"><button aria-pressed={alignment === "auto"} onClick={() => { setAlignment("auto"); setComparisonDirty(true); }} type="button">Find start automatically</button><button aria-pressed={alignment === "manual"} onClick={() => { setAlignment("manual"); setComparisonDirty(true); }} type="button">Adjust myself</button></div>{alignment === "manual" && <label className="coach-offset-label">Shift your voice (seconds)<div className="coach-stepper"><button aria-label="Move your voice 100 milliseconds earlier" onClick={() => { setOffset((Number(offset) - 0.1).toFixed(2)); setComparisonDirty(true); }} type="button">−0.1</button><input aria-label="Your voice alignment shift in seconds" inputMode="decimal" max={90} min={-90} onChange={(event) => { setOffset(event.target.value); setComparisonDirty(true); }} step={0.01} type="number" value={offset} /><button aria-label="Move your voice 100 milliseconds later" onClick={() => { setOffset((Number(offset) + 0.1).toFixed(2)); setComparisonDirty(true); }} type="button">+0.1</button></div><small>Positive moves your take later; negative moves it earlier.</small></label>}<label className="coach-transpose-label">Intentional key change<select aria-label="Transpose your voice for comparison" onChange={(event) => { setTranspose(Number(event.target.value)); setComparisonDirty(true); }} value={transpose}>{Array.from({ length: 25 }, (_, index) => index - 12).map((value) => <option key={value} value={value}>{value === 0 ? "Original pitch · no adjustment" : value === 12 ? "+12 · compare my voice one octave higher" : value === -12 ? "−12 · compare my voice one octave lower" : `${value > 0 ? "+" : ""}${value} semitones to my voice`}</option>)}</select></label><p className="coach-fineprint">Only use a key adjustment if you intentionally sang in a different key. It changes the pitch used for the score; playback remains your original voice.</p><button className="button-primary coach-analyse" disabled={processing || recording} onClick={() => void compare()} type="button">{compareBusy ? <LoaderCircle className="animate-spin" size={18} /> : <Sparkles size={18} />}{comparison ? "Update comparison" : "Compare these phrases"}</button></div>}
          </div>}
        </section>
        <p className="coach-privacy-note">Audio and analyses are temporary. Save recordings you want to keep. Your pitch is analysed after recording—not live.</p>
      </div>

      <section className="coach-results-column" ref={resultsRef} aria-labelledby="coach-results-heading">
        <div className="coach-results-heading"><div><p className="eyebrow">Your practice lens</p><h3 id="coach-results-heading">{analysis ? "A clearer picture of your singing" : "Every note has a story."}</h3></div>{takeJob && <button className="coach-icon-button" aria-label="Clear this practice session" disabled={processing || recording || takePending || referencePending} onClick={clearPractice} type="button"><RotateCcw size={17} /></button>}</div>
        {networkHint && <p className="coach-warning" role="status">{networkHint}</p>}
        {!analysis && <div className="coach-empty-state"><div className="coach-empty-icon"><Activity size={42} /></div><h4>{takePending || uploading === "take" ? "Finding the melody in your voice…" : "Start with 10–15 seconds."}</h4><p>{takePending || uploading === "take" ? "We look for the fundamental pitch, resolve brief octave mistakes, and keep the notes you actually sustain." : "Sing sa, re, ga, ma—or a favourite line. Your pitch will appear here, ready to explore one moment at a time."}</p><div className="coach-empty-features"><span><Check size={15} />Clean note contour</span><span><Check size={15} />Entries & steadiness</span><span><Check size={15} />Listen as you explore</span></div><p className="coach-fineprint">The graph will use your actual recording. No sample data is shown.</p></div>}
        {analysis && <>
          <div className="coach-metrics"><div><span>Detected range</span><strong>{analysis.summary.lowest_note && analysis.summary.highest_note ? `${analysis.summary.lowest_note} – ${analysis.summary.highest_note}` : "No clear range"}</strong></div><div><span>Voiced audio</span><strong>{analysis.summary.voiced_seconds.toFixed(1)}<small>s</small></strong></div><div><span>Note regions</span><strong>{analysis.summary.note_count}</strong></div></div>
          <div className="coach-card coach-graph-card"><div className="coach-graph-heading"><h4><Activity size={18} />{visibleComparison ? "Two voices, one phrase" : "Your clean pitch contour"}</h4>{comparison && <button className="coach-text-button" onClick={() => setShowComparison(!showComparison)} type="button">{showComparison ? "Show solo trace" : "Show comparison"}</button>}</div>{comparisonDirty && comparison && <p className="coach-warning">Alignment or key settings changed. Update comparison to apply them.</p>}<PitchGraph key={`${takeJob?.id}-${visibleComparison ? `${comparison?.alignment.offset_seconds}-${comparison?.transpose_semitones}` : "solo"}`} analysis={analysis} audioUrl={absoluteUrl(processorUrl, takeJob?.audio_url ?? null)} comparison={visibleComparison} referenceAnalysis={referenceAnalysis} referenceAudioUrl={absoluteUrl(processorUrl, referenceJob?.audio_url ?? null)} /></div>
          {analysis.warnings.length > 0 && <div className="coach-analysis-warnings">{analysis.warnings.map((warning) => <p className="coach-warning" key={warning}>{warning}</p>)}</div>}
          {comparison && !comparisonDirty && <div className="coach-card coach-score-card"><div className="coach-score-heading"><div><p className="eyebrow">Pitch match</p><p>{comparison.score !== null ? "For this phrase and these alignment settings" : "More confidence needed before a score"}</p></div><strong className={comparison.score === null ? "is-unscored" : ""}>{comparison.score === null ? "Unscored" : Math.round(comparison.score)}{comparison.score !== null && <small>/100</small>}</strong></div>{comparison.unscored_reason && <p className="coach-warning">{comparison.unscored_reason}</p>}<div className="coach-score-metrics"><div><span>Typical pitch error</span><strong>{comparison.metrics.median_absolute_cents === null ? "—" : `${Math.round(comparison.metrics.median_absolute_cents)}¢`}</strong></div><div><span>Reference covered</span><strong>{Math.round(comparison.metrics.reference_coverage_percent)}%</strong></div><div><span>Within ½ semitone</span><strong>{comparison.metrics.within_50_cents_percent === null ? "—" : `${Math.round(comparison.metrics.within_50_cents_percent)}%`}</strong></div></div><p className="coach-fineprint">Alignment: {comparison.alignment.mode === "auto" ? `automatic (${comparison.alignment.confidence} confidence)` : "manually set"} · Your voice shifted {comparison.alignment.offset_seconds >= 0 ? "+" : ""}{comparison.alignment.offset_seconds.toFixed(2)}s{comparison.transpose_semitones !== 0 ? ` · Pitch adjustment ${comparison.transpose_semitones > 0 ? "+" : ""}${comparison.transpose_semitones} semitones` : " · Original key"}. This measures pitch agreement, not expression, tone, or singing ability.</p><div className="coach-feedback-list">{comparison.feedback.map((item, index) => <div className={`coach-feedback is-${item.kind}`} key={`${item.title}-${index}`}><span>{item.kind === "strength" ? <Check size={17} /> : item.kind === "practice" ? <Activity size={17} /> : <Headphones size={17} />}</span><div><strong>{item.title}</strong><p>{item.detail}</p></div></div>)}</div></div>}
          {comparison && !comparisonDirty && comparison.note_feedback.length > 0 && <details className="coach-card coach-note-list"><summary>Compare each note <span>Reference timeline</span><ChevronDown size={17} /></summary><p className="coach-fineprint">Negative means below the reference; positive means above. Entry error includes any overall pitch bias. Low coverage makes a note less reliable.</p><div className="coach-note-table"><div className="coach-note-table-header"><span>Reference note</span><span>Typical error</span><span>Entry error</span></div>{comparison.note_feedback.map((note, index) => <div key={`${note.start}-${index}`}><span><strong>{note.note}</strong><small>{practiceTime(note.start)} · {Math.round(note.coverage_percent)}% covered</small></span><span>{note.median_error_cents === null ? "—" : `${note.median_error_cents >= 0 ? "+" : ""}${Math.round(note.median_error_cents)}¢`}</span><span>{note.attack_error_cents === null ? "—" : `${note.attack_error_cents >= 0 ? "+" : ""}${Math.round(note.attack_error_cents)}¢`}</span></div>)}</div></details>}
          {analysis.notes.length > 0 && <details className="coach-card coach-note-list"><summary>Explore your note entries <span>{analysis.notes.length} note regions</span><ChevronDown size={17} /></summary><p className="coach-fineprint">How each note begins compared with its own sustained pitch. Negative means you entered below; positive means above. Steadiness is typical variation around the held pitch (median absolute deviation). Vibrato and ornamentation can be deliberate.</p><div className="coach-note-table"><div className="coach-note-table-header"><span>Time / note</span><span>Entry</span><span>Steadiness</span></div>{analysis.notes.map((note, index) => <div key={`${note.start}-${index}`}><span><strong>{noteLabel(note.midi)}</strong><small>{practiceTime(note.start)}–{practiceTime(note.end)}</small></span><span>{note.attack_cents === null ? "—" : `${note.attack_cents >= 0 ? "+" : ""}${Math.round(note.attack_cents)}¢`}</span><span>{Math.round(note.stability_cents)}¢<small>spread</small></span></div>)}</div></details>}
          <p className="coach-privacy-note">Analysed clip: {takeJob?.source_name}, starting at {practiceTime(takeJob?.source_start_seconds ?? 0)} in the source. Times on the solo graph start at 0 for this phrase. Separation artefacts, breathy voices, harmonies, and expressive slides can affect estimates. This is pitch practice, not an assessment of raga correctness.</p>
        </>}
      </section>
    </div>
    {error && <div className="coach-error-toast" role="alert"><span>{error}</span><button aria-label="Dismiss singing coach error" onClick={() => setError("")} type="button"><X size={18} /></button></div>}
  </div>;
}
