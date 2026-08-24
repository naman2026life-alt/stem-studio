"use client";

import { ChangeEvent, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { CheckCircle2, Download, FileAudio, LoaderCircle, Music, UploadCloud, XCircle } from "lucide-react";
import * as tus from "tus-js-client";

import { createClient } from "@/lib/supabase/client";
import type { SeparationJob } from "@/lib/types";

const ACCEPTED = ["audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp4", "audio/flac", "audio/aac", "audio/ogg"];
const MAX_BYTES = 150 * 1024 * 1024;

function formatDate(value: string) {
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(value));
}

function StatusIcon({ status }: { status: SeparationJob["status"] }) {
  if (status === "completed") return <CheckCircle2 className="text-emerald-300" size={18} />;
  if (status === "failed") return <XCircle className="text-rose-300" size={18} />;
  return <LoaderCircle className="animate-spin text-violet-300" size={18} />;
}

export function Studio({ initialJobs, userId }: { initialJobs: SeparationJob[]; userId: string }) {
  const [jobs, setJobs] = useState(initialJobs);
  const [file, setFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [message, setMessage] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);
  const supabase = useMemo(() => createClient(), []);

  const refreshJobs = useCallback(async () => {
    const { data } = await supabase.from("separation_jobs").select("*").order("created_at", { ascending: false }).limit(12);
    if (data) setJobs(data as SeparationJob[]);
  }, [supabase]);

  useEffect(() => {
    const active = jobs.some((job) => job.status === "queued" || job.status === "processing");
    if (!active) return;
    const timer = window.setInterval(refreshJobs, 5000);
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
    if (!file) return;
    setUploading(true);
    setMessage("Uploading privately…");
    const jobId = crypto.randomUUID();
    const extension = file.name.split(".").pop()?.toLowerCase() || "audio";
    const sourcePath = `${userId}/jobs/${jobId}/source.${extension}`;

    const { data: sessionData } = await supabase.auth.getSession();
    const accessToken = sessionData.session?.access_token;
    if (!accessToken) {
      setMessage("Your session expired. Sign in again and retry.");
      setUploading(false);
      return;
    }

    try {
      const projectId = new URL(process.env.NEXT_PUBLIC_SUPABASE_URL!).hostname.split(".")[0];
      await new Promise<void>((resolve, reject) => {
        const upload = new tus.Upload(file, {
          endpoint: `https://${projectId}.storage.supabase.co/storage/v1/upload/resumable`,
          retryDelays: [0, 3000, 5000, 10000, 20000],
          headers: { authorization: `Bearer ${accessToken}`, "x-upsert": "false" },
          uploadDataDuringCreation: true,
          removeFingerprintOnSuccess: true,
          chunkSize: 6 * 1024 * 1024,
          metadata: {
            bucketName: "audio",
            objectName: sourcePath,
            contentType: file.type || "audio/mpeg",
            cacheControl: "3600",
          },
          onError: reject,
          onProgress(bytesUploaded, bytesTotal) {
            setMessage(`Uploading privately… ${Math.round((bytesUploaded / bytesTotal) * 100)}%`);
          },
          onSuccess: () => resolve(),
        });
        upload.findPreviousUploads().then((previous) => {
          if (previous.length) upload.resumeFromPreviousUpload(previous[0]);
          upload.start();
        }).catch(reject);
      });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Upload failed. Please retry.");
      setUploading(false);
      return;
    }

    const { error: jobError } = await supabase.from("separation_jobs").insert({
      id: jobId,
      user_id: userId,
      source_path: sourcePath,
      source_name: file.name,
    });

    if (jobError) {
      await supabase.storage.from("audio").remove([sourcePath]);
      setMessage(jobError.message);
    } else {
      setMessage("Queued. Your worker will pick this up shortly.");
      setFile(null);
      if (inputRef.current) inputRef.current.value = "";
      await refreshJobs();
    }
    setUploading(false);
  }

  async function download(path: string) {
    const { data, error } = await supabase.storage.from("audio").createSignedUrl(path, 60);
    if (error) return setMessage(error.message);
    window.open(data.signedUrl, "_blank", "noopener,noreferrer");
  }

  return (
    <section className="mt-10 grid gap-5 lg:grid-cols-[.85fr_1.15fr]">
      <article className="glass rounded-[2rem] p-7 sm:p-8">
        <p className="eyebrow">New separation</p>
        <h2 className="mt-3 text-2xl font-semibold tracking-tight text-white">Upload one song</h2>
        <p className="mt-2 text-sm leading-6 text-slate-400">MP3, WAV, M4A, FLAC, AAC, or OGG · up to 150 MB</p>
        <button className="upload-zone mt-7" onClick={() => inputRef.current?.click()} type="button">
          <UploadCloud size={28} />
          <span className="font-medium text-white">{file ? file.name : "Choose an audio file"}</span>
          <span className="text-xs text-slate-400">Files stay private to your account</span>
        </button>
        <input ref={inputRef} className="hidden" type="file" accept=".mp3,.wav,.m4a,.flac,.aac,.ogg,audio/*" onChange={chooseFile} />
        <button className="button-primary mt-4 w-full justify-center" disabled={!file || uploading} onClick={queueSeparation} type="button">
          {uploading ? <LoaderCircle className="animate-spin" size={18} /> : <Music size={18} />}
          {uploading ? "Uploading…" : "Separate this song"}
        </button>
        {message && <p className="mt-4 text-sm leading-6 text-slate-300">{message}</p>}
      </article>

      <article className="glass rounded-[2rem] p-7 sm:p-8">
        <div className="flex items-end justify-between gap-4">
          <div><p className="eyebrow">Recent work</p><h2 className="mt-3 text-2xl font-semibold tracking-tight text-white">Your separation jobs</h2></div>
          <button className="text-sm text-violet-300 hover:text-violet-200" onClick={refreshJobs} type="button">Refresh</button>
        </div>
        <div className="mt-6 space-y-3">
          {jobs.length === 0 && <div className="empty-state"><FileAudio size={28} /><p>Your first separation will appear here.</p></div>}
          {jobs.map((job) => (
            <div className="job-card" key={job.id}>
              <div className="flex min-w-0 items-start gap-3">
                <StatusIcon status={job.status} />
                <div className="min-w-0">
                  <p className="truncate text-sm font-medium text-white">{job.source_name}</p>
                  <p className="mt-1 text-xs capitalize text-slate-400">{job.status} · {job.progress}% · {formatDate(job.created_at)}</p>
                  {job.error && <p className="mt-2 text-xs leading-5 text-rose-300">{job.error}</p>}
                </div>
              </div>
              {job.status === "completed" && (
                <div className="mt-4 flex flex-wrap gap-2">
                  {[["Instrumental", job.instrumental_path], ["Drums", job.drums_path], ["Vocals", job.vocals_path]].map(([label, path]) => path && (
                    <button className="stem-button" key={label} onClick={() => download(path)} type="button"><Download size={13} />{label}</button>
                  ))}
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
