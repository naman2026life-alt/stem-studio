"use client";

import { ChangeEvent, useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, Download, FileAudio, LoaderCircle, Music, Play, UploadCloud, XCircle } from "lucide-react";

import type { SeparationJob } from "@/lib/types";

const ACCEPTED = ["audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp4", "audio/flac", "audio/aac", "audio/ogg"];
const MAX_BYTES = 150 * 1024 * 1024;
const SESSION_KEY = "stem-studio-job-ids";

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(value));
}

function StatusIcon({ status }: { status: SeparationJob["status"] }) {
  if (status === "completed") return <CheckCircle2 className="text-emerald-300" size={18} />;
  if (status === "failed") return <XCircle className="text-rose-300" size={18} />;
  return <LoaderCircle className="animate-spin text-violet-300" size={18} />;
}

function outputUrl(processorUrl: string, path: string) {
  return new URL(path, `${processorUrl}/`).toString();
}

function parseError(xhr: XMLHttpRequest) {
  try {
    const body = JSON.parse(xhr.responseText) as { detail?: string };
    return body.detail || `Upload failed (${xhr.status}).`;
  } catch {
    return `Upload failed (${xhr.status || "network error"}).`;
  }
}

export function Studio({ processorUrl }: { processorUrl: string }) {
  const [jobs, setJobs] = useState<SeparationJob[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

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

  function chooseFile(event: ChangeEvent<HTMLInputElement>) {
    const selected = event.target.files?.[0] ?? null;
    setMessage("");
    if (!selected) return setFile(null);
    if (selected.size > MAX_BYTES) return setMessage("Choose a file smaller than 150 MB.");
    if (!ACCEPTED.includes(selected.type) && !/\.(mp3|wav|m4a|flac|aac|ogg)$/i.test(selected.name)) {
      return setMessage("Use MP3, WAV, M4A, FLAC, AAC, or OGG.");
    }
    setFile(selected);
  }

  async function queueSeparation() {
    if (!file || !processorUrl) return;
    setUploading(true);
    setMessage("Preparing secure upload…");
    try {
      const tokenResponse = await fetch("/api/upload-token", { method: "POST" });
      if (!tokenResponse.ok) throw new Error("Could not authorize the upload. Please retry.");
      const token = await tokenResponse.json() as { timestamp?: string; signature?: string };
      const form = new FormData();
      form.append("file", file);

      const job = await new Promise<SeparationJob>((resolve, reject) => {
        const upload = new XMLHttpRequest();
        upload.open("POST", `${processorUrl}/jobs`);
        if (token.timestamp) upload.setRequestHeader("X-Stem-Timestamp", token.timestamp);
        if (token.signature) upload.setRequestHeader("X-Stem-Signature", token.signature);
        upload.upload.onprogress = (event) => {
          if (event.lengthComputable) setMessage(`Uploading… ${Math.round((event.loaded / event.total) * 100)}%`);
        };
        upload.onerror = () => reject(new Error("Could not reach the audio processor."));
        upload.onload = () => {
          if (upload.status >= 200 && upload.status < 300) resolve(JSON.parse(upload.responseText) as SeparationJob);
          else reject(new Error(parseError(upload)));
        };
        upload.send(form);
      });

      rememberJobs([job, ...jobs.filter((item) => item.id !== job.id)]);
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
      setMessage("Uploaded. Separation is now running.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Upload failed. Please retry.");
    } finally {
      setUploading(false);
    }
  }

  const configured = Boolean(processorUrl);

  return (
    <section className="mt-10 grid gap-5 lg:grid-cols-[.82fr_1.18fr]">
      <article className="glass rounded-[2rem] p-7 sm:p-8">
        <p className="eyebrow">New separation</p>
        <h2 className="mt-3 text-2xl font-semibold tracking-tight text-white">Upload one song</h2>
        <p className="mt-2 text-sm leading-6 text-slate-400">MP3, WAV, M4A, FLAC, AAC, or OGG · up to 150 MB</p>
        <button className="upload-zone mt-7" disabled={!configured || uploading} onClick={() => inputRef.current?.click()} type="button">
          <UploadCloud size={28} />
          <span className="max-w-full truncate font-medium text-white">{file ? file.name : "Choose an audio file"}</span>
          <span className="text-xs text-slate-400">The source and results expire automatically</span>
        </button>
        <input ref={inputRef} className="hidden" type="file" accept=".mp3,.wav,.m4a,.flac,.aac,.ogg,audio/*" onChange={chooseFile} />
        <button className="button-primary mt-4 w-full justify-center" disabled={!file || uploading || !configured} onClick={queueSeparation} type="button">
          {uploading ? <LoaderCircle className="animate-spin" size={18} /> : <Music size={18} />}
          {uploading ? "Uploading…" : "Separate this song"}
        </button>
        {!configured && (
          <div className="notice-card mt-4">
            <p className="font-medium text-amber-100">Processor connection pending</p>
            <p className="mt-1 text-xs leading-5 text-amber-100/70">The interface is ready. Add the processing service URL to enable uploads.</p>
          </div>
        )}
        {message && <p className="mt-4 text-sm leading-6 text-slate-300" role="status">{message}</p>}
      </article>

      <article className="glass rounded-[2rem] p-7 sm:p-8">
        <div className="flex items-end justify-between gap-4">
          <div><p className="eyebrow">This browser session</p><h2 className="mt-3 text-2xl font-semibold tracking-tight text-white">Your separation jobs</h2></div>
          {jobs.length > 0 && <button className="text-sm text-violet-300 hover:text-violet-200" onClick={() => void refreshJobs()} type="button">Refresh</button>}
        </div>
        <div className="mt-6 space-y-3">
          {jobs.length === 0 && <div className="empty-state"><FileAudio size={28} /><p>Your first separation will appear here.</p></div>}
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
                        <div className="flex items-center gap-2 text-sm font-medium text-slate-200"><Play size={14} />{label}</div>
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
