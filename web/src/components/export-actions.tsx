"use client";

import { Download, LoaderCircle, Share2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

type ExportActionsProps = {
  compact?: boolean;
  file?: File;
  fileName?: string;
  mimeType?: string;
  remoteUrl?: string;
  saveLabel?: string;
  secondaryFileName?: string;
  secondaryMimeType?: string;
  secondaryRemoteUrl?: string;
  secondarySaveLabel?: string;
  shareFileName?: string;
  shareMimeType?: string;
  shareRemoteUrl?: string;
  showHint?: boolean;
};

type ExportAction = "save" | "save-secondary" | "share" | null;
type ExportNotice = { kind: "error" | "info"; text: string } | null;

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
  saveLabel = "Save to device",
  secondaryFileName,
  secondaryMimeType,
  secondaryRemoteUrl,
  secondarySaveLabel = "Save MP3",
  shareFileName,
  shareMimeType,
  shareRemoteUrl,
  showHint = false,
}: ExportActionsProps) {
  const [busyAction, setBusyAction] = useState<ExportAction>(null);
  const [notice, setNotice] = useState<ExportNotice>(null);
  const preparedShareFile = useRef<File | null>(null);

  useEffect(() => {
    // Keep result cards light on mobile. Share files are fetched only after the
    // user asks for them instead of eagerly downloading every MP3 stem.
    preparedShareFile.current = null;
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
    return fallbackFile;
  };

  const resolveSecondaryDownloadFile = async () => {
    if (!secondaryRemoteUrl) throw new Error("No secondary file is available to export.");
    return await fetchFile(
      secondaryRemoteUrl,
      secondaryFileName || "stem-studio-mix.mp3",
      secondaryMimeType,
    );
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

  const saveSecondary = async () => {
    setBusyAction("save-secondary");
    setNotice(null);
    try {
      const exportFile = await resolveSecondaryDownloadFile();
      downloadFile(exportFile);
      setNotice({ kind: "info", text: "MP3 save started. Find it in this device’s Downloads or Files app." });
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
  const downloadName = file?.name || fileName || "audio file";
  const secondaryDownloadName = secondaryFileName || "MP3 audio file";
  const outgoingName = file?.name || shareFileName || fileName || "audio file";

  return (
    <div className={`export-panel${compact ? " is-compact" : ""}`}>
      <div className="export-actions">
        <button aria-label={`Save ${downloadName} to device`} className="export-button" disabled={busy} onClick={() => void save()} type="button">
          {busyAction === "save" ? <LoaderCircle className="animate-spin" size={16} /> : <Download size={16} />}
          {busyAction === "save" ? "Preparing…" : saveLabel}
        </button>
        {secondaryRemoteUrl && (
          <button aria-label={`Save ${secondaryDownloadName} to device`} className="export-button" disabled={busy} onClick={() => void saveSecondary()} type="button">
            {busyAction === "save-secondary" ? <LoaderCircle className="animate-spin" size={16} /> : <Download size={16} />}
            {busyAction === "save-secondary" ? "Preparing…" : secondarySaveLabel}
          </button>
        )}
        <button aria-label={`Share ${outgoingName} to WhatsApp`} className="export-button is-share" disabled={busy} onClick={() => void share()} type="button">
          {busyAction === "share" ? <LoaderCircle className="animate-spin" size={16} /> : <Share2 size={16} />}
          {busyAction === "share" ? "Preparing share…" : "Share to WhatsApp"}
        </button>
      </div>
      {showHint && !notice && (
        <p className="export-hint">On iPhone, Share opens the secure iOS share sheet with the audio attached—choose WhatsApp and a chat.</p>
      )}
      {notice && <p className={`export-notice${notice.kind === "error" ? " is-error" : ""}`} role="status">{notice.text}</p>}
    </div>
  );
}
