"use client";

import { Download, LoaderCircle, Share2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

type ExportActionsProps = {
  compact?: boolean;
  file?: File;
  fileName?: string;
  mimeType?: string;
  remoteUrl?: string;
  shareFileName?: string;
  shareMimeType?: string;
  shareRemoteUrl?: string;
  showHint?: boolean;
};

type ExportAction = "save" | "share" | null;
type ExportNotice = { kind: "error" | "info"; text: string } | null;
type SharePreparation = "failed" | "idle" | "preparing" | "ready";

class ExportFetchError extends Error {
  constructor(public status: number) {
    super(`Unable to prepare this file (${status}).`);
  }
}

function mimeTypeForName(fileName: string) {
  const extension = fileName.toLowerCase().split(".").pop();
  const types: Record<string, string> = {
    aac: "audio/aac",
    flac: "audio/flac",
    m4a: "audio/mp4",
    mp3: "audio/mpeg",
    mp4: "audio/mp4",
    ogg: "audio/ogg",
    wav: "audio/wav",
    webm: "audio/webm",
  };
  return extension ? types[extension] ?? "" : "";
}

function withUsefulMimeType(file: File) {
  const inferredType = mimeTypeForName(file.name);
  if ((!file.type || file.type === "application/octet-stream") && inferredType) {
    return new File([file], file.name, {
      lastModified: file.lastModified,
      type: inferredType,
    });
  }
  return file;
}

async function fetchFile(
  remoteUrl: string,
  fileName: string,
  mimeType?: string,
  signal?: AbortSignal,
) {
  const response = await fetch(remoteUrl, { signal });
  if (!response.ok) throw new ExportFetchError(response.status);

  const blob = await response.blob();
  const inferredType = mimeTypeForName(fileName);
  const resolvedType = mimeType || inferredType || blob.type || "application/octet-stream";
  return new File([blob], fileName, { type: resolvedType });
}

function downloadFile(file: File) {
  const objectUrl = URL.createObjectURL(file);
  const link = document.createElement("a");
  link.href = objectUrl;
  link.download = file.name;
  link.rel = "noopener";
  document.body.appendChild(link);
  link.click();
  link.remove();

  // WebKit can finish consuming a blob URL after the synthetic click returns.
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60_000);
}

function errorName(error: unknown) {
  if (error && typeof error === "object" && "name" in error && typeof error.name === "string") {
    return error.name;
  }
  return "";
}

export function ExportActions({
  compact = false,
  file,
  fileName,
  mimeType,
  remoteUrl,
  shareFileName,
  shareMimeType,
  shareRemoteUrl,
  showHint = false,
}: ExportActionsProps) {
  const [busyAction, setBusyAction] = useState<ExportAction>(null);
  const [notice, setNotice] = useState<ExportNotice>(null);
  const [sharePreparation, setSharePreparation] = useState<SharePreparation>(shareRemoteUrl ? "preparing" : "idle");
  const preparedShareFile = useRef<File | null>(null);

  useEffect(() => {
    if (!shareRemoteUrl) return;
    const controller = new AbortController();
    let active = true;

    const prepareShareFile = async () => {
      // Let hydration finish before feature detection, then avoid downloading
      // share copies in browsers that cannot attach files to a share sheet.
      await Promise.resolve();
      const shareProbe = new File([new Uint8Array()], "share-probe.mp3", { type: "audio/mpeg" });
      const supportsFileSharing = typeof navigator.share === "function"
        && typeof navigator.canShare === "function"
        && navigator.canShare({ files: [shareProbe] });
      if (!supportsFileSharing) {
        if (active) setSharePreparation("idle");
        return;
      }

      try {
        const prepared = await fetchFile(
          shareRemoteUrl,
          shareFileName || fileName || "stem-studio-audio.mp3",
          shareMimeType,
          controller.signal,
        );
        if (!active) return;
        preparedShareFile.current = prepared;
        setSharePreparation("ready");
      } catch (error) {
        if (!active || errorName(error) === "AbortError") return;
        setSharePreparation("failed");
      }
    };

    void prepareShareFile();
    return () => {
      active = false;
      controller.abort();
      preparedShareFile.current = null;
    };
  }, [fileName, shareFileName, shareMimeType, shareRemoteUrl]);

  const resolveDownloadFile = async () => {
    if (file) return withUsefulMimeType(file);
    if (!remoteUrl) throw new Error("No file is available to export.");
    return await fetchFile(remoteUrl, fileName || "stem-studio-audio.wav", mimeType);
  };

  const resolveShareFile = async () => {
    if (file) return withUsefulMimeType(file);
    if (preparedShareFile.current) return preparedShareFile.current;

    if (shareRemoteUrl) {
      try {
        const prepared = await fetchFile(
          shareRemoteUrl,
          shareFileName || fileName || "stem-studio-audio.mp3",
          shareMimeType,
        );
        preparedShareFile.current = prepared;
        setSharePreparation("ready");
        return prepared;
      } catch (error) {
        if (!(error instanceof ExportFetchError) || (error.status !== 404 && error.status !== 410)) {
          throw error;
        }
        // Jobs created just before this release have WAVs but no share MP3.
      }
    }

    const fallbackFile = await resolveDownloadFile();
    preparedShareFile.current = fallbackFile;
    setSharePreparation("ready");
    return fallbackFile;
  };

  const explainFailure = (error: unknown) => {
    if (error instanceof ExportFetchError && (error.status === 404 || error.status === 410)) {
      return "This temporary result has expired. Run isolation again to recreate it.";
    }
    return "Couldn’t prepare this file. Check your connection and try again.";
  };

  const save = async () => {
    setBusyAction("save");
    setNotice(null);
    try {
      const exportFile = await resolveDownloadFile();
      downloadFile(exportFile);
      setNotice({ kind: "info", text: "Save started. Find the file in this device’s Downloads or Files app." });
    } catch (error) {
      setNotice({ kind: "error", text: explainFailure(error) });
    } finally {
      setBusyAction(null);
    }
  };

  const share = async () => {
    const fileWasReady = Boolean(file || preparedShareFile.current);
    setBusyAction("share");
    setNotice(null);

    try {
      const exportFile = await resolveShareFile();
      const supportsFileSharing = typeof navigator.share === "function"
        && typeof navigator.canShare === "function"
        && navigator.canShare({ files: [exportFile] });

      if (!supportsFileSharing) {
        downloadFile(exportFile);
        setNotice({
          kind: "info",
          text: "This browser can’t attach audio to its share sheet, so the file was saved instead. Attach it from WhatsApp.",
        });
        return;
      }

      try {
        await navigator.share({ files: [exportFile], title: exportFile.name });
        setNotice({ kind: "info", text: "Sent to your device’s share sheet." });
      } catch (error) {
        const name = errorName(error);
        if (name === "AbortError") return;
        if (name === "NotAllowedError" && !fileWasReady && !file) {
          setNotice({ kind: "info", text: "The file is ready. Tap Share to WhatsApp once more to open the share sheet." });
          return;
        }
        setNotice({
          kind: "error",
          text: "Couldn’t open the share sheet. Save the file, then attach it in WhatsApp.",
        });
      }
    } catch (error) {
      setNotice({ kind: "error", text: explainFailure(error) });
    } finally {
      setBusyAction(null);
    }
  };

  const busy = busyAction !== null;
  const sharePreparing = sharePreparation === "preparing";
  const downloadName = file?.name || fileName || "audio file";
  const outgoingName = file?.name || shareFileName || fileName || "audio file";

  return (
    <div className={`export-panel${compact ? " is-compact" : ""}`}>
      <div className="export-actions">
        <button aria-label={`Save ${downloadName} to device`} className="export-button" disabled={busy} onClick={() => void save()} type="button">
          {busyAction === "save" ? <LoaderCircle className="animate-spin" size={16} /> : <Download size={16} />}
          {busyAction === "save" ? "Preparing…" : "Save to device"}
        </button>
        <button aria-label={`Share ${outgoingName} to WhatsApp`} className="export-button is-share" disabled={busy || sharePreparing} onClick={() => void share()} type="button">
          {busyAction === "share" || sharePreparing ? <LoaderCircle className="animate-spin" size={16} /> : <Share2 size={16} />}
          {busyAction === "share" ? "Opening…" : sharePreparing ? "Preparing share…" : "Share to WhatsApp"}
        </button>
      </div>
      {showHint && !notice && (
        <p className="export-hint">On iPhone, Share opens the secure iOS share sheet with the audio attached—choose WhatsApp and a chat.</p>
      )}
      {notice && <p className={`export-notice${notice.kind === "error" ? " is-error" : ""}`} role="status">{notice.text}</p>}
    </div>
  );
}
