"use client";

import { ChangeEvent, FormEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CheckCircle2,
  CircleStop,
  Clock3,
  FileAudio,
  Film,
  FolderOpen,
  LoaderCircle,
  Link2,
  LockKeyhole,
  Mic,
  MicOff,
  Music,
  Pause,
  Play,
  Plus,
  RotateCcw,
  Scissors,
  Smartphone,
  Trash2,
  UnlockKeyhole,
  WandSparkles,
  XCircle,
} from "lucide-react";

import { DurationPicker } from "@/components/duration-picker";
import { ExportActions } from "@/components/export-actions";
import type { SeparationJob, SeparationMode, YouTubeImportJob } from "@/lib/types";

const AUDIO_EXTENSIONS = /\.(mp3|wav|m4a|flac|aac|ogg|webm)$/i;
const VIDEO_EXTENSIONS = /\.(mp4|mov|m4v|mkv|webm|avi)$/i;
const FILE_PICKER_ACCEPT = [
  ".mp3", ".wav", ".m4a", ".flac", ".aac", ".ogg",
  ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi",
  "audio/*", "video/*", "audio/x-m4a", "audio/mp4", "video/quicktime",
].join(",");
const MAX_BYTES = 150 * 1024 * 1024;
const SESSION_KEY = "stem-studio-job-ids";
const YOUTUBE_IMPORT_SESSION_KEY = "stem-studio-youtube-import-id";
const YOUTUBE_VIDEO_ID = /^[A-Za-z0-9_-]{11}$/;

type MediaKind = "audio" | "video";
type BusyAction = "convert" | "trim" | "youtube" | SeparationMode | null;
type ProtectedAction = Exclude<BusyAction, null> | "youtube_cancel";
type RecordingState = "idle" | "requesting" | "recording" | "paused" | "processing";
type TrimPart = { id: number; start: string; end: string };
type PickerTarget = { field: "start" | "end"; partId: number; partIndex: number };
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

function formatRecordingTime(value: number) {
  const totalSeconds = Math.floor(value);
  return `${String(Math.floor(totalSeconds / 60)).padStart(2, "0")}:${String(totalSeconds % 60).padStart(2, "0")}`;
}

function formatTimeValue(value: number) {
  const totalSeconds = Math.max(0, Math.round(value));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return hours
    ? `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`
    : `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function mediaKind(file: File): MediaKind | null {
  if (file.type.startsWith("audio/")) return "audio";
  if (file.type.startsWith("video/")) return "video";
  if (VIDEO_EXTENSIONS.test(file.name)) return "video";
  if (AUDIO_EXTENSIONS.test(file.name)) return "audio";
  return null;
}

function isSupportedYouTubeVideoUrl(value: string) {
  try {
    const parsed = new URL(value.trim());
    const host = parsed.hostname.toLowerCase();
    if (parsed.protocol !== "https:" || parsed.username || parsed.password) return false;
    if (!["youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"].includes(host)) return false;
    const pathParts = parsed.pathname.split("/").filter(Boolean);
    if (host === "youtu.be") return pathParts.length === 1 && YOUTUBE_VIDEO_ID.test(pathParts[0]);
    if (parsed.pathname.replace(/\/$/, "") === "/watch") {
      const values = parsed.searchParams.getAll("v");
      return values.length === 1 && YOUTUBE_VIDEO_ID.test(values[0]);
    }
    return pathParts.length === 2
      && ["shorts", "embed"].includes(pathParts[0])
      && YOUTUBE_VIDEO_ID.test(pathParts[1]);
  } catch {
    return false;
  }
}

function recordingMimeType() {
  if (typeof MediaRecorder === "undefined") return "";
  return ["audio/mp4", "audio/webm;codecs=opus", "audio/webm", "audio/ogg;codecs=opus"]
    .find((type) => MediaRecorder.isTypeSupported(type)) ?? "";
}

function recordingExtension(mimeType: string) {
  if (mimeType.includes("mp4")) return "m4a";
  if (mimeType.includes("ogg")) return "ogg";
  if (mimeType.includes("wav")) return "wav";
  return "webm";
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

function stemFileName(
  sourceName: string,
  stem: "instrumental" | "drums" | "vocals",
  mode: SeparationJob["mode"],
  extension: "mp3" | "wav",
) {
  const leafName = sourceName.split(/[\\/]/).pop() || "track";
  const baseName = leafName.replace(/\.[^.]+$/, "") || "track";
  const outputName = mode === "karaoke" && stem === "instrumental" ? "karaoke" : stem;
  return `${baseName}-${outputName}.${extension}`;
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
  const [workingState, setWorkingState] = useState<"original" | "converted" | "imported" | "edited">("original");
  const [baseWorkingState, setBaseWorkingState] = useState<"original" | "converted" | "imported">("original");
  const [audioDuration, setAudioDuration] = useState(0);
  const [parts, setParts] = useState<TrimPart[]>([{ id: 1, start: "00:00", end: "00:09" }]);
  const [accessPassword, setAccessPassword] = useState("");
  const [passwordDraft, setPasswordDraft] = useState("");
  const [accessVerified, setAccessVerified] = useState(false);
  const [pendingAction, setPendingAction] = useState<ProtectedAction | null>(null);
  const [pickerTarget, setPickerTarget] = useState<PickerTarget | null>(null);
  const [busyAction, setBusyAction] = useState<BusyAction>(null);
  const [recordingState, setRecordingState] = useState<RecordingState>("idle");
  const [recordingSeconds, setRecordingSeconds] = useState(0);
  const [youtubeUrl, setYoutubeUrl] = useState("");
  const [youtubeRightsConfirmed, setYoutubeRightsConfirmed] = useState(false);
  const [youtubeImportId, setYoutubeImportId] = useState("");
  const [message, setMessage] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const unlockDialogRef = useRef<HTMLFormElement>(null);
  const unlockInputRef = useRef<HTMLInputElement>(null);
  const unlockTriggerRef = useRef<HTMLElement | null>(null);
  const pickerTriggerRef = useRef<HTMLButtonElement | null>(null);
  const mediaRecorderRef = useRef<MediaRecorder | null>(null);
  const microphoneStreamRef = useRef<MediaStream | null>(null);
  const recordedChunksRef = useRef<Blob[]>([]);
  const youtubePollAbortRef = useRef<AbortController | null>(null);
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

  useEffect(() => {
    if (recordingState !== "recording") return;
    const timer = window.setInterval(() => setRecordingSeconds((current) => current + 0.25), 250);
    return () => window.clearInterval(timer);
  }, [recordingState]);

  useEffect(() => {
    if (!pendingAction) return;
    const focusTimer = window.setTimeout(() => unlockInputRef.current?.focus(), 0);
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setPasswordDraft("");
        setPendingAction(null);
      }
      if (event.key !== "Tab") return;
      const focusable = Array.from(unlockDialogRef.current?.querySelectorAll<HTMLElement>(
        'button:not(:disabled), input:not(:disabled), [href], [tabindex]:not([tabindex="-1"])',
      ) ?? []);
      if (!focusable.length) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => {
      window.clearTimeout(focusTimer);
      window.removeEventListener("keydown", closeOnEscape);
      unlockTriggerRef.current?.focus();
    };
  }, [pendingAction]);

  useEffect(() => () => {
    const recorder = mediaRecorderRef.current;
    if (recorder && recorder.state !== "inactive") {
      recorder.ondataavailable = null;
      recorder.onstop = null;
      recorder.stop();
    }
    microphoneStreamRef.current?.getTracks().forEach((track) => track.stop());
  }, []);

  function resetParts() {
    setParts([{ id: nextPartId++, start: "00:00", end: "00:09" }]);
  }

  function stopMicrophoneTracks() {
    microphoneStreamRef.current?.getTracks().forEach((track) => track.stop());
    microphoneStreamRef.current = null;
  }

  function activateRecordedAudio(file: File) {
    setSourceFile(file);
    setSourceKind("audio");
    setBaseAudio(file);
    setWorkingAudio(file);
    setWorkingState("original");
    setBaseWorkingState("original");
    setAudioDuration(0);
    resetParts();
  }

  async function startRecording() {
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setMessage("This browser cannot record audio here. Update iOS or use the file picker instead.");
      return;
    }
    setRecordingState("requesting");
    setRecordingSeconds(0);
    setMessage("Waiting for microphone permission…");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          autoGainControl: false,
          echoCancellation: false,
          noiseSuppression: false,
        },
      });
      microphoneStreamRef.current = stream;
      recordedChunksRef.current = [];
      const preferredType = recordingMimeType();
      let recorder: MediaRecorder;
      try {
        recorder = new MediaRecorder(stream, {
          ...(preferredType ? { mimeType: preferredType } : {}),
          audioBitsPerSecond: 192000,
        });
      } catch {
        recorder = new MediaRecorder(stream);
      }
      mediaRecorderRef.current = recorder;
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) recordedChunksRef.current.push(event.data);
      };
      recorder.onerror = () => {
        stopMicrophoneTracks();
        setRecordingState("idle");
        setMessage("The recording stopped unexpectedly. Please try again.");
      };
      recorder.onstop = () => {
        const chunks = recordedChunksRef.current;
        const finalType = recorder.mimeType || preferredType || chunks[0]?.type || "audio/webm";
        stopMicrophoneTracks();
        mediaRecorderRef.current = null;
        setRecordingState("idle");
        if (!chunks.length) {
          setMessage("No audio was captured. Please check microphone permission and retry.");
          return;
        }
        const blob = new Blob(chunks, { type: finalType });
        const timestamp = new Date().toISOString().replace(/[:.]/g, "-");
        const file = new File(
          [blob],
          `stem-studio-recording-${timestamp}.${recordingExtension(finalType)}`,
          { type: finalType },
        );
        activateRecordedAudio(file);
        setMessage("Recording ready. Preview it below, then save, share, trim, or isolate it.");
      };
      recorder.start(1000);
      setRecordingState("recording");
      setMessage("Recording from this device. Tap Stop when you are finished.");
    } catch (error) {
      stopMicrophoneTracks();
      setRecordingState("idle");
      const name = error instanceof DOMException ? error.name : "";
      if (name === "NotAllowedError") {
        setMessage("Microphone access was blocked. Allow microphone access for this site in Safari or Chrome settings, then retry.");
      } else if (name === "NotFoundError") {
        setMessage("No microphone was found on this device.");
      } else {
        setMessage("Could not start the microphone. Please retry or choose an existing audio file.");
      }
    }
  }

  function pauseRecording() {
    const recorder = mediaRecorderRef.current;
    if (recorder?.state !== "recording") return;
    recorder.pause();
    setRecordingState("paused");
    setMessage("Recording paused.");
  }

  function resumeRecording() {
    const recorder = mediaRecorderRef.current;
    if (recorder?.state !== "paused") return;
    recorder.resume();
    setRecordingState("recording");
    setMessage("Recording resumed.");
  }

  function stopRecording() {
    const recorder = mediaRecorderRef.current;
    if (!recorder || recorder.state === "inactive") return;
    setRecordingState("processing");
    setMessage("Finishing your recording…");
    recorder.stop();
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
    setBaseWorkingState("original");
    if (kind === "audio") {
      setBaseAudio(selected);
      setWorkingAudio(selected);
    } else {
      setBaseAudio(null);
      setWorkingAudio(null);
    }
  }

  async function requestToken(password = accessPassword): Promise<UploadToken> {
    const tokenResponse = await fetch("/api/upload-token", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    if (tokenResponse.status === 401) {
      setAccessPassword("");
      setAccessVerified(false);
      throw new Error("The studio password is incorrect. Tap the action again to retry.");
    }
    if (!tokenResponse.ok) throw new Error("Could not authorize processing. Please retry.");
    if (accessProtected) setAccessVerified(true);
    return await tokenResponse.json() as UploadToken;
  }

  function tokenHeaders(token: UploadToken) {
    const headers: Record<string, string> = {};
    if (token.timestamp) headers["X-Stem-Timestamp"] = token.timestamp;
    if (token.signature) headers["X-Stem-Signature"] = token.signature;
    return headers;
  }

  async function runMediaTool(path: string, form: FormData, password?: string) {
    const token = await requestToken(password);
    const response = await fetch(`${processorUrl}${path}`, {
      method: "POST",
      headers: tokenHeaders(token),
      body: form,
    });
    if (!response.ok) throw new Error(await parseResponseError(response));
    return await response.blob();
  }

  const pollYouTubeImport = useCallback(async (jobId: string, signal?: AbortSignal): Promise<YouTubeImportJob> => {
    let connectionFailures = 0;
    for (let attempt = 0; attempt < 600; attempt += 1) {
      if (signal?.aborted) throw new DOMException("Import polling stopped.", "AbortError");
      let response: Response;
      try {
        response = await fetch(`${processorUrl}/imports/youtube/${jobId}`, { cache: "no-store", signal });
      } catch (error) {
        if (signal?.aborted) throw error;
        connectionFailures += 1;
        if (connectionFailures > 8) {
          throw new Error("The connection was interrupted. Refresh this page to reconnect to the same YouTube import.");
        }
        setMessage("Connection interrupted—reconnecting to the same YouTube import…");
        await new Promise((resolve) => window.setTimeout(resolve, Math.min(2000 * connectionFailures, 10000)));
        continue;
      }
      if (!response.ok) {
        if ([500, 502, 503, 504].includes(response.status) && connectionFailures < 8) {
          connectionFailures += 1;
          setMessage("The processor is waking back up—reconnecting to this import…");
          await new Promise((resolve) => window.setTimeout(resolve, Math.min(2000 * connectionFailures, 10000)));
          continue;
        }
        if (response.status === 404) localStorage.removeItem(YOUTUBE_IMPORT_SESSION_KEY);
        throw new Error(await parseResponseError(response));
      }
      connectionFailures = 0;
      const job = await response.json() as YouTubeImportJob;
      if (job.status === "completed") return job;
      if (job.status === "failed") {
        localStorage.removeItem(YOUTUBE_IMPORT_SESSION_KEY);
        throw new Error(job.error || "YouTube audio import failed. Please retry.");
      }
      if (job.status === "queued") {
        setMessage("YouTube import queued on the private processor…");
      } else if (job.status === "waiting_for_helper") {
        setMessage("YouTube blocked the cloud route. Waiting for your private Mac helper—it will take over automatically while the Mac is awake.");
      } else if (job.status === "processing_home") {
        setMessage("Your private Mac helper is downloading the audio and preparing the MP3…");
      } else {
        setMessage("Downloading the YouTube audio and preparing an MP3… You can reopen this page to reconnect.");
      }
      const pollDelay = job.status === "waiting_for_helper" ? 6000 : 2000;
      await new Promise((resolve) => window.setTimeout(resolve, pollDelay));
    }
    throw new Error("This YouTube import exceeded its processing window. Please try again.");
  }, [processorUrl]);

  const activateYouTubeImport = useCallback(async (completed: YouTubeImportJob, signal?: AbortSignal) => {
    if (!completed.file_url) throw new Error("The imported MP3 is not available.");
    let fileResponse: Response;
    try {
      fileResponse = await fetch(outputUrl(processorUrl, completed.file_url), { cache: "no-store", signal });
    } catch (error) {
      if (signal?.aborted) throw error;
      throw new Error("The MP3 is ready, but the download was interrupted. Refresh this page to reconnect to the same import.");
    }
    if (!fileResponse.ok) {
      if (fileResponse.status === 404) localStorage.removeItem(YOUTUBE_IMPORT_SESSION_KEY);
      throw new Error(await parseResponseError(fileResponse));
    }
    const blob = await fileResponse.blob();
    const imported = new File([blob], completed.file_name || "youtube-audio.mp3", { type: "audio/mpeg" });
    setSourceFile(imported);
    setSourceKind("audio");
    setBaseAudio(imported);
    setWorkingAudio(imported);
    setWorkingState("imported");
    setBaseWorkingState("imported");
    setAudioDuration(0);
    setParts([{ id: nextPartId++, start: "00:00", end: "00:09" }]);
    localStorage.removeItem(YOUTUBE_IMPORT_SESSION_KEY);
    setYoutubeImportId("");
    setYoutubeUrl("");
    setYoutubeRightsConfirmed(false);
    setMessage("YouTube audio imported. The MP3 is now ready to preview, save, trim, or isolate.");
  }, [processorUrl]);

  async function importFromYouTube(password?: string) {
    if (!processorUrl) return;
    const controller = new AbortController();
    youtubePollAbortRef.current?.abort();
    youtubePollAbortRef.current = controller;
    setBusyAction("youtube");
    try {
      const existingJobId = localStorage.getItem(YOUTUBE_IMPORT_SESSION_KEY);
      if (existingJobId) {
        setYoutubeImportId(existingJobId);
        setMessage("Reconnecting to your current YouTube import…");
        const existing = await pollYouTubeImport(existingJobId, controller.signal);
        await activateYouTubeImport(existing, controller.signal);
        return;
      }
      if (!youtubeUrl.trim() || !youtubeRightsConfirmed || !isSupportedYouTubeVideoUrl(youtubeUrl)) return;
      setMessage("Starting a private YouTube audio import…");
      const token = await requestToken(password);
      const form = new FormData();
      form.append("url", youtubeUrl.trim());
      form.append("rights_confirmed", "true");
      const response = await fetch(`${processorUrl}/imports/youtube`, {
        method: "POST",
        headers: tokenHeaders(token),
        body: form,
        signal: controller.signal,
      });
      if (!response.ok) throw new Error(await parseResponseError(response));
      const queued = await response.json() as YouTubeImportJob;
      localStorage.setItem(YOUTUBE_IMPORT_SESSION_KEY, queued.id);
      setYoutubeImportId(queued.id);
      const completed = await pollYouTubeImport(queued.id, controller.signal);
      await activateYouTubeImport(completed, controller.signal);
    } catch (error) {
      if (!controller.signal.aborted) {
        setMessage(error instanceof Error ? error.message : "YouTube audio import failed. Please retry.");
      }
    } finally {
      if (youtubePollAbortRef.current === controller) youtubePollAbortRef.current = null;
      setBusyAction(null);
    }
  }

  async function cancelYouTubeImport(password?: string) {
    const jobId = localStorage.getItem(YOUTUBE_IMPORT_SESSION_KEY);
    if (!jobId || !processorUrl) return;
    setMessage("Cancelling this YouTube import…");
    try {
      const token = await requestToken(password);
      const response = await fetch(`${processorUrl}/imports/youtube/${jobId}/cancel`, {
        method: "POST",
        headers: tokenHeaders(token),
      });
      if (!response.ok && response.status !== 404) throw new Error(await parseResponseError(response));
      youtubePollAbortRef.current?.abort();
      youtubePollAbortRef.current = null;
      localStorage.removeItem(YOUTUBE_IMPORT_SESSION_KEY);
      setYoutubeImportId("");
      setBusyAction(null);
      setMessage("YouTube import cancelled. You can start another one.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not cancel this import. Please retry.");
    }
  }

  useEffect(() => {
    const storedJobId = localStorage.getItem(YOUTUBE_IMPORT_SESSION_KEY);
    if (!processorUrl || !storedJobId) return;
    const controller = new AbortController();
    youtubePollAbortRef.current?.abort();
    youtubePollAbortRef.current = controller;
    const resume = async () => {
      try {
        const completed = await pollYouTubeImport(storedJobId, controller.signal);
        await activateYouTubeImport(completed, controller.signal);
      } catch (error) {
        if (!controller.signal.aborted) {
          setMessage(error instanceof Error ? error.message : "Could not reconnect to the YouTube import.");
        }
      } finally {
        if (!controller.signal.aborted) setBusyAction(null);
      }
    };
    const timer = window.setTimeout(() => {
      if (controller.signal.aborted) return;
      setYoutubeImportId(storedJobId);
      setBusyAction("youtube");
      setMessage("Reconnecting to your YouTube import…");
      void resume();
    }, 0);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
      if (youtubePollAbortRef.current === controller) youtubePollAbortRef.current = null;
    };
  }, [activateYouTubeImport, pollYouTubeImport, processorUrl]);

  async function convertVideo(password?: string) {
    if (!sourceFile || sourceKind !== "video" || !processorUrl) return;
    setBusyAction("convert");
    setMessage("Uploading the video and extracting its audio…");
    try {
      const form = new FormData();
      form.append("file", sourceFile);
      const blob = await runMediaTool("/tools/extract-mp3", form, password);
      const name = `${sourceFile.name.replace(VIDEO_EXTENSIONS, "") || "video"}.mp3`;
      const converted = new File([blob], name, { type: "audio/mpeg" });
      setBaseAudio(converted);
      setWorkingAudio(converted);
      setWorkingState("converted");
      setBaseWorkingState("converted");
      setAudioDuration(0);
      resetParts();
      setMessage("Video converted. The MP3 is ready to edit, save, share, or isolate.");
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

  function openDurationPicker(target: PickerTarget, trigger: HTMLButtonElement) {
    pickerTriggerRef.current = trigger;
    setPickerTarget(target);
  }

  const closeDurationPicker = useCallback(() => {
    setPickerTarget(null);
    window.setTimeout(() => pickerTriggerRef.current?.focus(), 0);
  }, []);

  function applyDurationPicker(seconds: number) {
    if (!pickerTarget) return;
    updatePart(pickerTarget.partId, pickerTarget.field, formatTimeValue(seconds));
    closeDurationPicker();
  }

  function useTrackEnd() {
    if (!pickerTarget || pickerTarget.field !== "end") return;
    updatePart(pickerTarget.partId, "end", "");
    closeDurationPicker();
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

  async function trimAndMerge(password?: string) {
    if (!workingAudio || !processorUrl) return;
    setBusyAction("trim");
    setMessage("Trimming the parts in order and joining them…");
    try {
      const normalized = parsedParts();
      const form = new FormData();
      form.append("file", workingAudio);
      form.append("segments", JSON.stringify(normalized));
      const blob = await runMediaTool("/tools/trim-merge", form, password);
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
    setWorkingState(baseWorkingState);
    setAudioDuration(0);
    resetParts();
    setMessage("Restored the full audio file.");
  }

  async function queueSeparation(mode: SeparationMode, password?: string) {
    if (!workingAudio || !processorUrl) return;
    setBusyAction(mode);
    setMessage("Preparing the secure audio upload…");
    try {
      const token = await requestToken(password);
      const form = new FormData();
      form.append("file", workingAudio);
      form.append("mode", mode);

      const job = await new Promise<SeparationJob>((resolve, reject) => {
        const upload = new XMLHttpRequest();
        upload.open("POST", `${processorUrl}/jobs`);
        for (const [name, value] of Object.entries(tokenHeaders(token))) upload.setRequestHeader(name, value);
        upload.upload.onprogress = (event) => {
          if (event.lengthComputable) setMessage(`Uploading for ${mode === "karaoke" ? "karaoke mode" : "isolation"}… ${Math.round((event.loaded / event.total) * 100)}%`);
        };
        upload.onerror = () => reject(new Error("Could not reach the audio processor."));
        upload.onload = () => {
          if (upload.status >= 200 && upload.status < 300) resolve(JSON.parse(upload.responseText) as SeparationJob);
          else reject(new Error(parseUploadError(upload)));
        };
        upload.send(form);
      });

      rememberJobs([job, ...jobs.filter((item) => item.id !== job.id)]);
      setMessage(mode === "karaoke"
        ? "Uploaded. Your no-vocals karaoke track is now being created."
        : "Uploaded. Instrumental, drums, and vocals are now being isolated.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Upload failed. Please retry.");
    } finally {
      setBusyAction(null);
    }
  }

  function executeAction(action: ProtectedAction, password?: string) {
    if (action === "youtube") void importFromYouTube(password);
    if (action === "youtube_cancel") void cancelYouTubeImport(password);
    if (action === "convert") void convertVideo(password);
    if (action === "trim") void trimAndMerge(password);
    if (action === "stems") void queueSeparation("stems", password);
    if (action === "karaoke") void queueSeparation("karaoke", password);
  }

  function runProtectedAction(action: ProtectedAction) {
    if (accessProtected && !accessVerified) {
      unlockTriggerRef.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
      setPasswordDraft("");
      setPendingAction(action);
      return;
    }
    executeAction(action);
  }

  function submitUnlock(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const password = passwordDraft.trim();
    if (!pendingAction || !password) return;
    const action = pendingAction;
    setAccessPassword(password);
    setPasswordDraft("");
    setPendingAction(null);
    executeAction(action, password);
  }

  const configured = Boolean(processorUrl);
  const busy = busyAction !== null || recordingState !== "idle";
  const youtubeUrlValid = isSupportedYouTubeVideoUrl(youtubeUrl);
  const usableAudioDuration = Number.isFinite(audioDuration) && audioDuration > 0 ? audioDuration : 0;
  const pickerPart = pickerTarget ? parts.find((part) => part.id === pickerTarget.partId) : null;
  const pickerRawValue = pickerPart && pickerTarget ? pickerPart[pickerTarget.field] : "";
  const pickerParsedValue = parseTime(
    pickerRawValue,
    pickerTarget?.field === "end" && usableAudioDuration > 0 ? usableAudioDuration : 0,
  );
  const pickerInitialSeconds = typeof pickerParsedValue === "number" && Number.isFinite(pickerParsedValue)
    ? pickerParsedValue
    : 0;

  return (
    <section className="studio-grid mt-10 grid gap-5 xl:grid-cols-[1.08fr_.92fr]">
      <article className="glass rounded-[2rem] p-6 sm:p-8">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div>
            <p className="eyebrow">Media workspace</p>
            <h2 className="mt-3 text-2xl font-semibold tracking-tight text-white">Record, upload, or import—then edit and isolate</h2>
          </div>
          <span className="limit-pill">150 MB max</span>
        </div>

        <div className="source-choice-grid mt-7">
          <button
            aria-label="Add audio or video from this device"
            className="upload-zone"
            disabled={!configured || busy}
            onClick={() => inputRef.current?.click()}
            type="button"
          >
            <span className="upload-plus"><Plus size={25} strokeWidth={2.2} /></span>
            <span className="max-w-full truncate font-medium text-white">{sourceFile ? sourceFile.name : "Add an existing file"}</span>
            <span className="text-xs text-slate-400">Browse Files, iCloud Drive, Downloads, or On My iPhone</span>
            <span className="file-types">MP3, M4A, WAV, FLAC, AAC, OGG · MP4, MOV, M4V, MKV, WEBM, AVI</span>
          </button>

          <section className={`recorder-zone recorder-${recordingState}`} aria-label="Record audio in Stem Studio">
            <span className="recorder-mark"><Mic size={25} /></span>
            <span className="font-medium text-white">
              {recordingState === "recording" ? "Recording now" : recordingState === "paused" ? "Recording paused" : "Record in Stem Studio"}
            </span>
            {recordingState === "idle" && (
              <>
                <span className="text-xs leading-5 text-slate-400">Use this iPhone’s microphone—no Voice Recorder export required</span>
                <button className="record-button" disabled={busyAction !== null} onClick={() => void startRecording()} type="button"><Mic size={16} />Start recording</button>
              </>
            )}
            {(recordingState === "requesting" || recordingState === "processing") && (
              <span className="recorder-working"><LoaderCircle className="animate-spin" size={17} />{recordingState === "requesting" ? "Requesting microphone…" : "Finishing recording…"}</span>
            )}
            {(recordingState === "recording" || recordingState === "paused") && (
              <>
                <span className="recording-timer" aria-live="polite"><span />{formatRecordingTime(recordingSeconds)}</span>
                <div className="recorder-controls">
                  {recordingState === "recording"
                    ? <button className="recorder-control" onClick={pauseRecording} type="button"><Pause size={16} />Pause</button>
                    : <button className="recorder-control" onClick={resumeRecording} type="button"><Play size={16} />Resume</button>}
                  <button className="recorder-stop" onClick={stopRecording} type="button"><CircleStop size={16} />Stop & use</button>
                </div>
              </>
            )}
            <span className="recorder-privacy">Stays in this browser until you process, save, or share it</span>
          </section>
        </div>
        <input
          ref={inputRef}
          aria-label="Audio or video file"
          className="hidden"
          type="file"
          accept={FILE_PICKER_ACCEPT}
          onChange={chooseFile}
          onClick={(event) => { event.currentTarget.value = ""; }}
        />

        <details aria-busy={busyAction === "youtube"} className="youtube-import mt-4">
          <summary><Link2 size={18} />Import audio from a YouTube link <span>Private</span></summary>
          <form
            className="youtube-import-body"
            onSubmit={(event) => {
              event.preventDefault();
              runProtectedAction("youtube");
            }}
          >
            <div>
              <label htmlFor="youtube-url">YouTube video link</label>
              <input
                autoCapitalize="none"
                autoComplete="url"
                autoCorrect="off"
                disabled={busy}
                id="youtube-url"
                inputMode="url"
                onChange={(event) => setYoutubeUrl(event.target.value)}
                placeholder="https://www.youtube.com/watch?v=…"
                spellCheck={false}
                type="url"
                value={youtubeUrl}
              />
            </div>
            {youtubeUrl.trim() && !youtubeUrlValid && <p className="youtube-import-error">Paste a link to one YouTube video—not a playlist, channel, or another website.</p>}
            <p className="youtube-import-note">One public video, up to 20 minutes. It becomes an MP3 and then works like any uploaded audio. If your phone reloads the page, Stem Studio reconnects automatically.</p>
            <label className="youtube-rights-check">
              <input
                checked={youtubeRightsConfirmed}
                disabled={busy}
                onChange={(event) => setYoutubeRightsConfirmed(event.target.checked)}
                type="checkbox"
              />
              <span>I own this audio or have permission or authorization to download and process it.</span>
            </label>
            <button
              className="button-primary w-full justify-center"
              disabled={busy || !configured || !youtubeUrlValid || !youtubeRightsConfirmed}
              type="submit"
            >
              {busyAction === "youtube" ? <LoaderCircle className="animate-spin" size={18} /> : <Link2 size={18} />}
              {busyAction === "youtube" ? "Importing audio…" : "Import & use audio"}
            </button>
            {busyAction === "youtube" && <p className="youtube-import-status">{message || "Preparing the audio… Keep this tab open."}</p>}
            {busyAction === "youtube" && youtubeImportId && (
              <button className="button-ghost w-full justify-center" onClick={() => runProtectedAction("youtube_cancel")} type="button">
                <XCircle size={17} />Cancel this import
              </button>
            )}
            <p className="youtube-import-note">No Google account or cookies are used. If YouTube blocks the cloud route, your private Mac helper takes over automatically while that Mac is awake.</p>
            {accessProtected && !accessVerified && <p className="processing-hint"><LockKeyhole size={13} />Private processing—password requested after you tap.</p>}
          </form>
        </details>

        <details className="phone-help mt-4">
          <summary><FolderOpen size={17} />Already recorded in another app?</summary>
          <div className="phone-help-body">
            <p><span className="help-number">1</span><span className="help-copy">In Apple Voice Memos, tap <strong>Share</strong>, then choose <strong>Save to Files</strong> in the iOS Share sheet.</span></p>
            <p><span className="help-number">2</span><span className="help-copy">For third-party Recorder apps, use their own <strong>Share or Export</strong> action if they provide one. iOS does not let this website read another app’s private recordings.</span></p>
            <p><FolderOpen className="help-icon" size={16} /><span className="help-copy">Then tap <strong>Add an existing file</strong> above. M4A and MP3 both work directly.</span></p>
          </div>
        </details>

        <details className="phone-help mt-3">
          <summary><Smartphone size={17} />Install Stem Studio like an app</summary>
          <div className="phone-help-body">
            <p><span className="help-number">1</span><span className="help-copy">Open this site in Safari, tap <strong>Share</strong>, then <strong>Add to Home Screen</strong>.</span></p>
            <p><span className="help-number">2</span><span className="help-copy">Open Stem Studio from its new Home Screen icon and record directly here next time.</span></p>
          </div>
        </details>

        {accessProtected && accessVerified && (
          <div className="unlock-status mt-4">
            <span><UnlockKeyhole size={15} />Processing unlocked for this page</span>
            <button onClick={() => { setAccessPassword(""); setAccessVerified(false); }} type="button">Change password</button>
          </div>
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
              disabled={busy}
              onClick={() => runProtectedAction("convert")}
              type="button"
            >
              {busyAction === "convert" ? <LoaderCircle className="animate-spin" size={18} /> : <Film size={18} />}
              {busyAction === "convert" ? "Converting…" : "Convert video to MP3"}
            </button>
            {accessProtected && !accessVerified && <p className="processing-hint"><LockKeyhole size={13} />Private processing—password requested after you tap.</p>}
          </section>
        )}

        {workingAudio && audioUrl && (
          <section className="workflow-card mt-5" aria-labelledby="audio-heading">
            <div className="step-heading">
              <span className="step-number">{sourceKind === "video" ? "2" : "1"}</span>
              <div className="min-w-0 flex-1">
                <h3 id="audio-heading" className="truncate">{workingAudio.name}</h3>
                <p>{workingState === "edited" ? "Trimmed and merged MP3" : workingState === "converted" ? "MP3 extracted from video" : workingState === "imported" ? "MP3 imported from YouTube" : "Original audio"} · {formatDuration(audioDuration)}</p>
              </div>
            </div>
            <audio
              className="mt-4 w-full"
              controls
              onDurationChange={(event) => setAudioDuration(event.currentTarget.duration)}
              onLoadedMetadata={(event) => setAudioDuration(event.currentTarget.duration)}
              preload="metadata"
              src={audioUrl}
            />
            <div className="active-audio-actions mt-4">
              <ExportActions file={workingAudio} key={`${workingAudio.name}-${workingAudio.size}-${workingAudio.lastModified}`} showHint />
              {workingState === "edited" && <button className="button-ghost" disabled={busy} onClick={restoreBaseAudio} type="button"><RotateCcw size={16} />Restore full audio</button>}
            </div>
          </section>
        )}

        {workingAudio && (
          <section className="workflow-card mt-5" aria-labelledby="trim-heading">
            <div className="step-heading">
              <span className="step-number">{sourceKind === "video" ? "3" : "2"}</span>
              <div><h3 id="trim-heading">Choose parts to keep</h3><p>Tap a time to use the scroll wheels, or type seconds, MM:SS, or HH:MM:SS.</p></div>
            </div>
            <div className="mt-4 space-y-3">
              {parts.map((part, index) => (
                <div className="part-row" key={part.id}>
                  <span className="part-label">Part {index + 1}</span>
                  <div className="part-time-field">
                    <span>Start</span>
                    <div className="part-time-control">
                      <input className="duration-manual-input" aria-label={`Part ${index + 1} start`} inputMode="decimal" onChange={(event) => updatePart(part.id, "start", event.target.value)} placeholder="00:00" value={part.start} />
                      <button
                        aria-label={`Set Part ${index + 1} start with scroll wheels`}
                        className="duration-picker-trigger"
                        disabled={busy}
                        onClick={(event) => openDurationPicker({ field: "start", partId: part.id, partIndex: index }, event.currentTarget)}
                        type="button"
                      >
                        <span className="duration-picker-value">{part.start || "00:00"}</span><Clock3 size={16} />
                      </button>
                    </div>
                  </div>
                  <span className="part-arrow">→</span>
                  <div className="part-time-field">
                    <span>End</span>
                    <div className="part-time-control">
                      <input className="duration-manual-input" aria-label={`Part ${index + 1} end`} inputMode="decimal" onChange={(event) => updatePart(part.id, "end", event.target.value)} placeholder={audioDuration ? formatDuration(audioDuration) : "End"} value={part.end} />
                      <button
                        aria-label={`Set Part ${index + 1} end with scroll wheels`}
                        className="duration-picker-trigger"
                        disabled={busy}
                        onClick={(event) => openDurationPicker({ field: "end", partId: part.id, partIndex: index }, event.currentTarget)}
                        type="button"
                      >
                        <span className="duration-picker-value">{part.end || "End of track"}</span><Clock3 size={16} />
                      </button>
                    </div>
                  </div>
                  <button aria-label={`Remove part ${index + 1}`} className="icon-button" disabled={parts.length === 1 || busy} onClick={() => removePart(part.id)} type="button"><Trash2 size={16} /></button>
                </div>
              ))}
            </div>
            <p className="mt-3 text-xs leading-5 text-slate-500">Times beyond {audioDuration ? formatDuration(audioDuration) : "the end"} are automatically capped at the end of the audio. Leave End blank to use the full remaining track.</p>
            <div className="mt-4 grid gap-2 sm:grid-cols-2">
              <button className="button-ghost justify-center" disabled={busy || parts.length >= 50} onClick={addPart} type="button"><Plus size={16} />Add another part</button>
              <button className="button-primary justify-center" disabled={busy} onClick={() => runProtectedAction("trim")} type="button">
                {busyAction === "trim" ? <LoaderCircle className="animate-spin" size={18} /> : <Scissors size={17} />}
                {busyAction === "trim" ? "Trimming…" : "Trim & merge parts"}
              </button>
            </div>
            {accessProtected && !accessVerified && <p className="processing-hint"><LockKeyhole size={13} />Private processing—password requested after you tap.</p>}
          </section>
        )}

        {workingAudio && (
          <section className="workflow-card mt-5" aria-labelledby="isolate-heading">
            <div className="step-heading">
              <span className="step-number">{sourceKind === "video" ? "4" : "3"}</span>
              <div><h3 id="isolate-heading">Choose what to create</h3><p>Make one karaoke track with the vocals removed, or split the audio into all three useful tracks.</p></div>
            </div>
            <div className="active-source-chip mt-4">
              <FileAudio size={16} />
              <span>
                <small>{workingState === "edited" ? "Using trimmed & merged audio" : workingState === "converted" ? "Using audio from video" : workingState === "imported" ? "Using imported YouTube audio" : "Using full audio"}</small>
                <strong>{workingAudio.name}</strong>
              </span>
            </div>
            <div className="separation-mode-grid mt-3">
              <article className="separation-mode-card is-karaoke">
                <div className="separation-mode-topline">
                  <span className="separation-mode-icon"><MicOff size={20} /></span>
                  <span className="separation-mode-badge">Best for singing</span>
                </div>
                <div>
                  <h4>Karaoke mode</h4>
                  <p>Remove detected vocals and keep the music as one ready-to-sing backing track.</p>
                </div>
                <button
                  aria-label={`Remove vocals from ${workingAudio.name} and make a karaoke track`}
                  className="button-primary w-full justify-center"
                  disabled={busy || !configured}
                  onClick={() => runProtectedAction("karaoke")}
                  type="button"
                >
                  {busyAction === "karaoke" ? <LoaderCircle className="animate-spin" size={18} /> : <MicOff size={18} />}
                  {busyAction === "karaoke" ? "Uploading…" : "Make karaoke track"}
                </button>
              </article>
              <article className="separation-mode-card">
                <div className="separation-mode-topline">
                  <span className="separation-mode-icon"><WandSparkles size={20} /></span>
                  <span className="separation-mode-badge is-muted">Full split</span>
                </div>
                <div>
                  <h4>All 3 tracks</h4>
                  <p>Create instrumental/no-vocals, drums-only, and vocals-only files together.</p>
                </div>
                <button
                  aria-label={`Create instrumental, drums, and vocals tracks from ${workingAudio.name}`}
                  className="button-ghost w-full justify-center"
                  disabled={busy || !configured}
                  onClick={() => runProtectedAction("stems")}
                  type="button"
                >
                  {busyAction === "stems" ? <LoaderCircle className="animate-spin" size={18} /> : <WandSparkles size={18} />}
                  {busyAction === "stems" ? "Uploading…" : "Create all 3 tracks"}
                </button>
              </article>
            </div>
            {accessProtected && !accessVerified && <p className="processing-hint"><LockKeyhole size={13} />Private processing—password requested after you tap.</p>}
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
          <div><p className="eyebrow">Temporary results</p><h2 className="mt-3 text-2xl font-semibold tracking-tight text-white">Your processed audio</h2></div>
          {jobs.length > 0 && <button className="text-sm text-violet-300 hover:text-violet-200" onClick={() => void refreshJobs()} type="button">Refresh</button>}
        </div>
        <div className="mt-6 space-y-3">
          {jobs.length === 0 && <div className="empty-state"><FileAudio size={28} /><p>Your karaoke tracks and stems will appear here.</p></div>}
          {jobs.map((job) => {
            const jobMode: SeparationMode = job.mode === "karaoke" ? "karaoke" : "stems";
            const resultStems: readonly ("instrumental" | "drums" | "vocals")[] = jobMode === "karaoke"
              ? ["instrumental"]
              : ["instrumental", "drums", "vocals"];
            return (
              <div className="job-card" key={job.id}>
                <div className="flex min-w-0 items-start gap-3">
                  <StatusIcon status={job.status} />
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-medium text-white">{job.source_name}</p>
                    <p aria-live="polite" className="mt-1 text-xs capitalize text-slate-400">{jobMode === "karaoke" ? "Karaoke" : "3 tracks"} · {job.status} · {job.progress}% · {formatDate(job.created_at)}</p>
                    {job.error && <p className="mt-2 text-xs leading-5 text-rose-300">{job.error}</p>}
                  </div>
                </div>
                {job.status === "completed" && (
                  <div className="mt-5 grid gap-3">
                    {resultStems.map((stem) => {
                      const path = job[`${stem}_url`];
                      if (!path) return null;
                      const url = outputUrl(processorUrl, path);
                      const label = jobMode === "karaoke" && stem === "instrumental"
                        ? "Karaoke track · vocals removed"
                        : stem === "instrumental" ? "Instrumental / no vocals" : stem[0].toUpperCase() + stem.slice(1);
                      return (
                        <div className={`output-row ${jobMode === "karaoke" ? "karaoke-result" : ""}`} key={stem}>
                          <div className="flex items-center gap-2 text-sm font-medium text-slate-200"><Music size={14} />{label}</div>
                          <audio aria-label={`${label} preview for ${job.source_name}`} className="h-9 min-w-0 flex-1" controls preload="none" src={url} />
                          <ExportActions
                            compact
                            fileName={stemFileName(job.source_name, stem, jobMode, "wav")}
                            mimeType="audio/wav"
                            remoteUrl={url}
                            shareFileName={stemFileName(job.source_name, stem, jobMode, "mp3")}
                            shareMimeType="audio/mpeg"
                            shareRemoteUrl={outputUrl(processorUrl, `/jobs/${job.id}/share/${stem}`)}
                          />
                        </div>
                      );
                    })}
                    <p className="text-xs leading-5 text-slate-500">
                      {jobMode === "karaoke"
                        ? "Your karaoke track is ready to preview, save, or share. Save it before this temporary job expires."
                        : "Save these files before this temporary job expires. Share opens your device’s share sheet with the audio attached—choose WhatsApp and a chat."}
                    </p>
                  </div>
                )}
                {(job.status === "queued" || job.status === "processing") && (
                  <div
                    aria-label={`${jobMode === "karaoke" ? "Karaoke" : "Stem separation"} progress`}
                    aria-valuemax={100}
                    aria-valuemin={0}
                    aria-valuenow={job.progress}
                    className="progress-track mt-4"
                    role="progressbar"
                  >
                    <span style={{ width: `${Math.max(job.progress, 4)}%` }} />
                  </div>
                )}
              </div>
            );
          })}
        </div>
      </article>

      {pendingAction && (
        <div className="unlock-backdrop">
          <form
            aria-describedby="unlock-description"
            aria-labelledby="unlock-title"
            aria-modal="true"
            className="unlock-dialog"
            onSubmit={submitUnlock}
            ref={unlockDialogRef}
            role="dialog"
          >
            <span className="unlock-icon"><LockKeyhole size={22} /></span>
            <h2 id="unlock-title">Unlock private processing</h2>
            <p id="unlock-description">
              Enter the Stem Studio password to {pendingAction === "trim"
                ? "trim and merge these parts"
                : pendingAction === "convert"
                  ? "convert this video"
                  : pendingAction === "karaoke"
                    ? "remove the vocals and make a karaoke track"
                    : pendingAction === "youtube"
                      ? "privately import this YouTube audio"
                      : pendingAction === "youtube_cancel"
                        ? "cancel this YouTube import"
                      : "create all three isolated tracks"}. It protects your Modal credits from public use.
            </p>
            <label>
              Studio password
              <input
                autoComplete="current-password"
                onChange={(event) => setPasswordDraft(event.target.value)}
                placeholder="Enter the private studio password"
                ref={unlockInputRef}
                type="password"
                value={passwordDraft}
              />
            </label>
            <div className="unlock-actions">
              <button className="button-ghost justify-center" onClick={() => { setPasswordDraft(""); setPendingAction(null); }} type="button">Cancel</button>
              <button className="button-primary justify-center" disabled={!passwordDraft.trim()} type="submit">
                <UnlockKeyhole size={17} />Unlock & continue
              </button>
            </div>
            <p className="unlock-note">Kept only in this browser tab—never stored in the audio file.</p>
          </form>
        </div>
      )}

      {pickerTarget && pickerPart && (
        <DurationPicker
          allowTrackEnd={pickerTarget.field === "end"}
          initialSeconds={pickerInitialSeconds}
          label={`Part ${pickerTarget.partIndex + 1} · ${pickerTarget.field === "start" ? "Start" : "End"}`}
          maxSeconds={pickerTarget.field === "start" && usableAudioDuration > 1 ? usableAudioDuration - 1 : usableAudioDuration}
          onCancel={closeDurationPicker}
          onConfirm={applyDurationPicker}
          onUseTrackEnd={useTrackEnd}
        />
      )}
    </section>
  );
}
