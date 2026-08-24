"use client";

import { ChangeEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CheckCircle2,
  Download,
  FileAudio,
  Film,
  LoaderCircle,
  Music,
  Plus,
  RotateCcw,
  Scissors,
  Trash2,
  UploadCloud,
  WandSparkles,
  XCircle,
} from "lucide-react";

import type { SeparationJob } from "@/lib/types";

const AUDIO_EXTENSIONS = /\.(mp3|wav|m4a|flac|aac|ogg)$/i;
const VIDEO_EXTENSIONS = /\.(mp4|mov|m4v|mkv|webm|avi)$/i;
const MAX_BYTES = 150 * 1024 * 1024;
const SESSION_KEY = "stem-studio-job-ids";

type MediaKind = "audio" | "video";
type BusyAction = "convert" | "trim" | "separate" | null;
type TrimPart = { id: number; start: string; end: string };
type UploadToken = { timestamp?: string; signature?: string };

let nextPartId = 2;

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(new Date(value));
}

function formatDuration(value: number) {
  if (!Number.isFinite(value) || value <= 0) return "Reading duration…";
  const totalSeconds = Math.round(value);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return hours
    ? `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
    : `${minutes}:${String(seconds).padStart(2, "0")}`;
}

function mediaKind(file: File): MediaKind | null {
  if (file.type.startsWith("audio/") || AUDIO_EXTENSIONS.test(file.name)) return "audio";
  if (file.type.startsWith("video/") || VIDEO_EXTENSIONS.test(file.name)) return "video";
  return null;
}

function parseTime(value: string, emptyValue: number | null) {
  const trimmed = value.trim();
  if (!trimmed) return emptyValue;
  const parts = trimmed.split(":");
  if (parts.length > 3 || parts.some((part) => part.trim() === "")) return Number.NaN;
  const numbers = parts.map(Number);
  if (numbers.some((part) => !Number.isFinite(part) || part < 0)) return Number.NaN;
  if (parts.length === 1) return numbers[0];
  if (parts.length === 2) return numbers[0] * 60 + numbers[1];
  return numbers[0] * 3600 + numbers[1] * 60 + numbers[2];
}

function useObjectUrl(file: File | null) {
  const url = useMemo(() => file ? URL.createObjectURL(file) : "", [file]);
  useEffect(() => () => {
    if (url) URL.revokeObjectURL(url);
  }, [url]);
  return url;
}

function StatusIcon({ status }: { status: SeparationJob["status"] }) {
  if (status === "completed") return <CheckCircle2 className="text-emerald-300" size={18} />;
  if (status === "failed") return <XCircle className="text-rose-300" size={18} />;
  return <LoaderCircle className="animate-spin text-violet-300" size={18} />;
}

function outputUrl(processorUrl: string, path: string) {
  return new URL(path, `${processorUrl}/`).toString();
}

function parseUploadError(xhr: XMLHttpRequest) {
  try {
    const body = JSON.parse(xhr.responseText) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    return `Upload failed (${xhr.status}).`;
  } catch {
    return `Upload failed (${xhr.status || "network error"}).`;
  }
}

async function parseResponseError(response: Response) {
  try {
    const body = await response.json() as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
  } catch {
    // Fall through to a status-based message when the processor did not return JSON.
  }
  return `Processing failed (${response.status}).`;
}

export function Studio({ accessProtected, processorUrl }: { accessProtected: boolean; processorUrl: string }) {
  const [jobs, setJobs] = useState<SeparationJob[]>([]);
  const [sourceFile, setSourceFile] = useState<File | null>(null);
  const [sourceKind, setSourceKind] = useState<MediaKind | null>(null);
  const [baseAudio, setBaseAudio] = useState<File | null>(null);
  const [workingAudio, setWorkingAudio] = useState<File | null>(null);
  const [workingState, setWorkingState] = useState<"original" | "converted" | "edited">("original");
  const [audioDuration, setAudioDuration] = useState(0);
  const [parts, setParts] = useState<TrimPart[]>([{ id: 1, start: "00:00", end: "00:09" }]);
  const [accessPassword, setAccessPassword] = useState("");
  const [busyAction, setBusyAction] = useState<BusyAction>(null);
  const [message, setMessage] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const sourceUrl = useObjectUrl(sourceFile);
  const audioUrl = useObjectUrl(workingAudio);

  const rememberJobs = useCallback((nextJobs: SeparationJob[]) => {
    setJobs(nextJobs);
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(nextJobs.map((job) => job.id)));
  }, []);

  const refreshJobs = useCallback(async (knownJobs?: SeparationJob[]) => {
    if (!processorUrl) return;
    const current = knownJobs ?? jobs;
    const storedIds = current.length
      ? current.map((job) => job.id)
      : JSON.parse(sessionStorage.getItem(SESSION_KEY) || "[]") as string[];
    if (!storedIds.length) return;
    const results = await Promise.all(storedIds.map(async (id) => {
      try {
        const response = await fetch(`${processorUrl}/jobs/${id}`, { cache: "no-store" });
        return response.ok ? await response.json() as SeparationJob : null;
      } catch {
        return current.find((job) => job.id === id) ?? null;
      }
    }));
    rememberJobs(results.filter((job): job is SeparationJob => Boolean(job)));
  }, [jobs, processorUrl, rememberJobs]);

  useEffect(() => {
    let cancelled = false;
    const restore = async () => {
      if (!processorUrl) return;
      const ids = JSON.parse(sessionStorage.getItem(SESSION_KEY) || "[]") as string[];
      const results = await Promise.all(ids.map(async (id) => {
        try {
          const response = await fetch(`${processorUrl}/jobs/${id}`, { cache: "no-store" });
          return response.ok ? await response.json() as SeparationJob : null;
        } catch {
          return null;
        }
      }));
      if (!cancelled) rememberJobs(results.filter((job): job is SeparationJob => Boolean(job)));
    };
    const timer = window.setTimeout(() => void restore(), 0);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [processorUrl, rememberJobs]);

  useEffect(() => {
    const active = jobs.some((job) => job.status === "queued" || job.status === "processing");
    if (!active) return;
    const timer = window.setInterval(() => void refreshJobs(), 3000);
    return () => window.clearInterval(timer);
  }, [jobs, refreshJobs]);

  function resetParts() {
    setParts([{ id: nextPartId++, start: "00:00", end: "00:09" }]);
  }

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0] ?? null;
    setMessage("");
    setAudioDuration(0);
    resetParts();
    if (!selected) {
      setSourceFile(null);
      setSourceKind(null);
      setBaseAudio(null);
      setWorkingAudio(null);
      return;
    }
    if (selected.size > MAX_BYTES) {
      event.target.value = "";
      return setMessage("Choose a file smaller than 150 MB.");
    }
    const kind = mediaKind(selected);
    if (!kind) {
      event.target.value = "";
      return setMessage("Use a common audio file or MP4, MOV, M4V, MKV, WEBM, or AVI video.");
    }
    setSourceFile(selected);
    setSourceKind(kind);
    setWorkingState("original");
    if (kind === "audio") {
      setBaseAudio(selected);
      setWorkingAudio(selected);
    } else {
      setBaseAudio(null);
      setWorkingAudio(null);
    }
  }

  async function requestToken(): Promise<UploadToken> {
    const tokenResponse = await fetch("/api/upload-token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: accessPassword }),
    });
    if (tokenResponse.status === 401) throw new Error("The studio password is incorrect.");
    if (!tokenResponse.ok) throw new Error("Could not authorize processing. Please retry.");
    return await tokenResponse.json() as UploadToken;
  }

  function tokenHeaders(token: UploadToken) {
    const headers: Record<string, string> = {};
    if (token.timestamp) headers["X-Stem-Timestamp"] = token.timestamp;
    if (token.signature) headers["X-Stem-Signature"] = token.signature;
    return headers;
  }

  async function runMediaTool(path: string, form: FormData) {
    const token = await requestToken();
    const response = await fetch(`${processorUrl}${path}`, {
      method: "POST",
      headers: tokenHeaders(token),
      body: form,
    });
    if (!response.ok) throw new Error(await parseResponseError(response));
    return await response.blob();
  }

  async function convertVideo() {
    if (!sourceFile || sourceKind !== "video" || !processorUrl) return;
    setBusyAction("convert");
    setMessage("Uploading the video and extracting its audio…");
    try {
      const form = new FormData();
      form.append("file", sourceFile);
      const blob = await runMediaTool("/tools/extract-mp3", form);
      const name = `${sourceFile.name.replace(VIDEO_EXTENSIONS, "") || "video"}.mp3`;
      const converted = new File([blob], name, { type: "audio/mpeg" });
      setBaseAudio(converted);
      setWorkingAudio(converted);
      setWorkingState("converted");
      setAudioDuration(0);
      resetParts();
      setMessage("Video converted. The MP3 is ready to edit, download, or isolate.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Video conversion failed. Please retry.");
    } finally {
      setBusyAction(null);
    }
  }

  function updatePart(id: number, field: "start" | "end", value: string) {
    setParts((current) => current.map((part) => part.id === id ? { ...part, [field]: value } : part));
  }

  function addPart() {
    setParts((current) => [...current, { id: nextPartId++, start: "00:00", end: "" }]);
  }

  function removePart(id: number) {
    setParts((current) => current.filter((part) => part.id !== id));
  }

  function parsedParts() {
    return parts.map((part, index) => {
      const rawStart = parseTime(part.start, 0);
      const rawEnd = parseTime(part.end, null);
      if (rawStart === null || Number.isNaN(rawStart)) throw new Error(`Part ${index + 1} has an invalid start time.`);
      if (Number.isNaN(rawEnd)) throw new Error(`Part ${index + 1} has an invalid end time.`);
      const start = audioDuration > 0 ? Math.min(rawStart, audioDuration) : rawStart;
      const end = rawEnd === null ? null : audioDuration > 0 ? Math.min(rawEnd, audioDuration) : rawEnd;
      const effectiveEnd = end ?? (audioDuration > 0 ? audioDuration : null);
      if (effectiveEnd !== null && effectiveEnd <= start) throw new Error(`Part ${index + 1} must end after it starts.`);
      return { start_seconds: start, end_seconds: end };
    });
  }

  async function trimAndMerge() {
    if (!workingAudio || !processorUrl) return;
    setBusyAction("trim");
    setMessage("Trimming the parts in order and joining them…");
    try {
      const normalized = parsedParts();
      const form = new FormData();
      form.append("file", workingAudio);
      form.append("segments", JSON.stringify(normalized));
      const blob = await runMediaTool("/tools/trim-merge", form);
      const name = `${workingAudio.name.replace(AUDIO_EXTENSIONS, "") || "audio"}-trimmed.mp3`;
      setWorkingAudio(new File([blob], name, { type: "audio/mpeg" }));
      setWorkingState("edited");
      setAudioDuration(0);
      resetParts();
      setMessage("Trimmed and merged. This MP3 is now the active file for isolation.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Trim and merge failed. Please retry.");
    } finally {
      setBusyAction(null);
    }
  }

  function restoreBaseAudio() {
    if (!baseAudio) return;
    setWorkingAudio(baseAudio);
    setWorkingState(sourceKind === "video" ? "converted" : "original");
    setAudioDuration(0);
    resetParts();
    setMessage("Restored the full audio file.");
  }

  async function queueSeparation() {
    if (!workingAudio || !processorUrl) return;
    setBusyAction("separate");
    setMessage("Preparing the secure audio upload…");
    try {
      const token = await requestToken();
      const form = new FormData();
      form.append("file", workingAudio);

      const job = await new Promise<SeparationJob>((resolve, reject) => {
        const upload = new XMLHttpRequest();
        upload.open("POST", `${processorUrl}/jobs`);
        for (const [name, value] of Object.entries(tokenHeaders(token))) upload.setRequestHeader(name, value);
        upload.upload.onprogress = (event) => {
          if (event.lengthComputable) setMessage(`Uploading for isolation… ${Math.round((event.loaded / event.total) * 100)}%`);
        };
        upload.onerror = () => reject(new Error("Could not reach the audio processor."));
        upload.onload = () => {
          if (upload.status >= 200 && upload.status < 300) resolve(JSON.parse(upload.responseText) as SeparationJob);
          else reject(new Error(parseUploadError(upload)));
        };
        upload.send(form);
      });

      rememberJobs([job, ...jobs.filter((item) => item.id !== job.id)]);
      setMessage("Uploaded. Instrumental, drums, and vocals are now being isolated.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Upload failed. Please retry.");
    } finally {
      setBusyAction(null);
    }
  }

  const configured = Boolean(processorUrl);
  const busy = busyAction !== null;

  return (
    <section className="mt-10 grid gap-5 xl:grid-cols-[1.08fr_.92fr]">
      <article className="glass rounded-[2rem] p-6 sm:p-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="eyebrow">Media workspace</p>
            <h2 className="mt-3 text-2xl font-semibold tracking-tight text-white">Upload, edit, then isolate</h2>
          </div>
          <span className="limit-pill">150 MB max</span>
        </div>

        <button className="upload-zone mt-7" disabled={!configured || busy} onClick={() => inputRef.current?.click()} type="button">
          <UploadCloud size={28} />
          <span className="max-w-full truncate font-medium text-white">{sourceFile ? sourceFile.name : "Choose audio or video"}</span>
          <span className="text-xs text-slate-400">Audio: MP3, WAV, M4A, FLAC, AAC, OGG · Video: MP4, MOV, M4V, MKV, WEBM, AVI</span>
        </button>
        <input
          ref={inputRef}
          className="hidden"
          type="file"
          accept=".mp3,.wav,.m4a,.flac,.aac,.ogg,.mp4,.mov,.m4v,.mkv,.webm,.avi,audio/*,video/*"
          onChange={chooseFile}
        />

        {accessProtected && (
          <label className="mt-4 block text-xs font-medium uppercase tracking-[.12em] text-slate-400">
            Studio password
            <input
              autoComplete="current-password"
              className="password-input mt-2"
              onChange={(event) => setAccessPassword(event.target.value)}
              placeholder="Enter the private studio password"
              type="password"
              value={accessPassword}
            />
          </label>
        )}

        {sourceKind === "video" && sourceUrl && (
          <section className="workflow-card mt-5" aria-labelledby="video-heading">
            <div className="step-heading">
              <span className="step-number">1</span>
              <div><h3 id="video-heading">Video selected</h3><p>Preview it, then optionally extract the audio as a high-quality MP3.</p></div>
            </div>
            <video className="media-preview mt-4" controls preload="metadata" src={sourceUrl} />
            <button
              className="button-primary mt-4 w-full justify-center"
              disabled={busy || (accessProtected && !accessPassword)}
              onClick={convertVideo}
              type="button"
            >
              {busyAction === "convert" ? <LoaderCircle className="animate-spin" size={18} /> : <Film size={18} />}
              {busyAction === "convert" ? "Converting…" : "Convert video to MP3"}
            </button>
          </section>
        )}

        {workingAudio && audioUrl && (
          <section className="workflow-card mt-5" aria-labelledby="audio-heading">
            <div className="step-heading">
              <span className="step-number">{sourceKind === "video" ? "2" : "1"}</span>
              <div className="min-w-0 flex-1">
                <h3 id="audio-heading" className="truncate">{workingAudio.name}</h3>
                <p>{workingState === "edited" ? "Trimmed and merged MP3" : workingState === "converted" ? "MP3 extracted from video" : "Original audio"} · {formatDuration(audioDuration)}</p>
              </div>
            </div>
            <audio
              className="mt-4 w-full"
              controls
              onLoadedMetadata={(event) => setAudioDuration(event.currentTarget.duration)}
              preload="metadata"
              src={audioUrl}
            />
            <div className="mt-4 flex flex-wrap gap-2">
              <a className="button-ghost" download={workingAudio.name} href={audioUrl}><Download size={16} />Download active audio</a>
              {workingState === "edited" && <button className="button-ghost" disabled={busy} onClick={restoreBaseAudio} type="button"><RotateCcw size={16} />Restore full audio</button>}
            </div>
          </section>
        )}

        {workingAudio && (
          <section className="workflow-card mt-5" aria-labelledby="trim-heading">
            <div className="step-heading">
              <span className="step-number">{sourceKind === "video" ? "3" : "2"}</span>
              <div><h3 id="trim-heading">Choose parts to keep</h3><p>Parts are joined in this order. Use seconds, MM:SS, or HH:MM:SS.</p></div>
            </div>
            <div className="mt-4 space-y-3">
              {parts.map((part, index) => (
                <div className="part-row" key={part.id}>
                  <span className="part-label">Part {index + 1}</span>
                  <label>Start<input aria-label={`Part ${index + 1} start`} inputMode="decimal" onChange={(event) => updatePart(part.id, "start", event.target.value)} placeholder="00:00" value={part.start} /></label>
                  <span className="part-arrow">→</span>
                  <label>End<input aria-label={`Part ${index + 1} end`} inputMode="decimal" onChange={(event) => updatePart(part.id, "end", event.target.value)} placeholder={audioDuration ? formatDuration(audioDuration) : "End"} value={part.end} /></label>
                  <button aria-label={`Remove part ${index + 1}`} className="icon-button" disabled={parts.length === 1 || busy} onClick={() => removePart(part.id)} type="button"><Trash2 size={16} /></button>
                </div>
              ))}
            </div>
            <p className="mt-3 text-xs leading-5 text-slate-500">Times beyond {audioDuration ? formatDuration(audioDuration) : "the end"} are automatically capped at the end of the audio. Leave End blank to use the full remaining track.</p>
            <div className="mt-4 grid gap-2 sm:grid-cols-2">
              <button className="button-ghost justify-center" disabled={busy || parts.length >= 50} onClick={addPart} type="button"><Plus size={16} />Add another part</button>
              <button className="button-primary justify-center" disabled={busy || (accessProtected && !accessPassword)} onClick={trimAndMerge} type="button">
                {busyAction === "trim" ? <LoaderCircle className="animate-spin" size={18} /> : <Scissors size={17} />}
                {busyAction === "trim" ? "Trimming…" : "Trim & merge parts"}
              </button>
            </div>
          </section>
        )}

        {workingAudio && (
          <section className="workflow-card mt-5" aria-labelledby="isolate-heading">
            <div className="step-heading">
              <span className="step-number">{sourceKind === "video" ? "4" : "3"}</span>
              <div><h3 id="isolate-heading">Isolate three useful tracks</h3><p>Use the active audio above—full or edited—to create instrumental, drums, and vocals.</p></div>
            </div>
            <button
              className="button-primary mt-4 w-full justify-center"
              disabled={busy || !configured || (accessProtected && !accessPassword)}
              onClick={queueSeparation}
              type="button"
            >
              {busyAction === "separate" ? <LoaderCircle className="animate-spin" size={18} /> : <WandSparkles size={18} />}
              {busyAction === "separate" ? "Uploading…" : "Isolate instrumental, drums & vocals"}
            </button>
          </section>
        )}

        {!configured && (
          <div className="notice-card mt-4">
            <p className="font-medium text-amber-100">Processor connection pending</p>
            <p className="mt-1 text-xs leading-5 text-amber-100/70">The interface is ready. Add the processing service URL to enable uploads.</p>
          </div>
        )}
        {message && <p className="status-message mt-4" role="status">{message}</p>}
      </article>

      <article className="glass self-start rounded-[2rem] p-6 sm:p-8">
        <div className="flex items-end justify-between gap-4">
          <div><p className="eyebrow">Temporary results</p><h2 className="mt-3 text-2xl font-semibold tracking-tight text-white">Your isolation jobs</h2></div>
          {jobs.length > 0 && <button className="text-sm text-violet-300 hover:text-violet-200" onClick={() => void refreshJobs()} type="button">Refresh</button>}
        </div>
        <div className="mt-6 space-y-3">
          {jobs.length === 0 && <div className="empty-state"><FileAudio size={28} /><p>Your first set of stems will appear here.</p></div>}
          {jobs.map((job) => (
            <div className="job-card" key={job.id}>
              <div className="flex min-w-0 items-start gap-3">
                <StatusIcon status={job.status} />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-white">{job.source_name}</p>
                  <p className="mt-1 text-xs capitalize text-slate-400">{job.status} · {job.progress}% · {formatDate(job.created_at)}</p>
                  {job.error && <p className="mt-2 text-xs leading-5 text-rose-300">{job.error}</p>}
                </div>
              </div>
              {job.status === "completed" && (
                <div className="mt-5 grid gap-3">
                  {(["instrumental", "drums", "vocals"] as const).map((stem) => {
                    const path = job[`${stem}_url`];
                    if (!path) return null;
                    const url = outputUrl(processorUrl, path);
                    const label = stem === "instrumental" ? "Instrumental / no vocals" : stem[0].toUpperCase() + stem.slice(1);
                    return (
                      <div className="output-row" key={stem}>
                        <div className="flex items-center gap-2 text-sm font-medium text-slate-200"><Music size={14} />{label}</div>
                        <audio className="h-9 min-w-0 flex-1" controls preload="none" src={url} />
                        <a className="stem-button" download href={url}><Download size={13} /><span className="hidden sm:inline">Download</span></a>
                      </div>
                    );
                  })}
                  <p className="text-xs text-slate-500">Download these files before this temporary job expires.</p>
                </div>
              )}
              {(job.status === "queued" || job.status === "processing") && <div className="progress-track mt-4"><span style={{ width: `${Math.max(job.progress, 4)}%` }} /></div>}
            </div>
          ))}
        </div>
      </article>
    </section>
  );
}
