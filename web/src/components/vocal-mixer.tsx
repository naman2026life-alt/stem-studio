"use client";

import { ChangeEvent, FormEvent, useState } from "react";
import { FileAudio, LoaderCircle, Mic2, Music2, SlidersHorizontal, WandSparkles } from "lucide-react";

import { ExportActions } from "@/components/export-actions";
import type { VocalMixResult } from "@/lib/types";

const MIXER_AUDIO_ACCEPT = ".mp3,.wav,.m4a,.flac,.aac,.ogg,.webm,audio/*,audio/x-m4a,audio/mp4";
const MIXER_MAX_BYTES = 150 * 1024 * 1024;

export type MixerInstrumentalOption = {
  id: string;
  label: string;
};

export type VocalMixRequest = {
  instrumentalFile?: File;
  instrumentalJobId?: string;
  vocalFile: File;
  offsetMs: number;
  trimStartSeconds: number;
  trimEndSeconds: number;
  vocalGainDb: number;
  instrumentalGainDb: number;
};

type VocalSource = "active" | "upload";

function chooseAudioFile(
  event: ChangeEvent<HTMLInputElement>,
  setter: (file: File | null) => void,
  setError: (message: string) => void,
) {
  const selected = event.target.files?.[0] ?? null;
  setError("");
  if (!selected) {
    setter(null);
    return;
  }
  if (selected.size > MIXER_MAX_BYTES) {
    event.target.value = "";
    setError("Choose an audio file smaller than 150 MB.");
    return;
  }
  setter(selected);
}

export function VocalMixer({
  activeAudio,
  busy,
  configured,
  instrumentalOptions,
  onCreate,
  onInstrumentalChange,
  processorUrl,
  result,
  selectedInstrumental,
}: {
  activeAudio: File | null;
  busy: boolean;
  configured: boolean;
  instrumentalOptions: MixerInstrumentalOption[];
  onCreate: (request: VocalMixRequest) => void;
  onInstrumentalChange: (value: string) => void;
  processorUrl: string;
  result: VocalMixResult | null;
  selectedInstrumental: string;
}) {
  const [instrumentalFile, setInstrumentalFile] = useState<File | null>(null);
  const [vocalFile, setVocalFile] = useState<File | null>(null);
  const [vocalSource, setVocalSource] = useState<VocalSource>("upload");
  const [offsetMs, setOffsetMs] = useState(0);
  const [trimStartSeconds, setTrimStartSeconds] = useState(0);
  const [trimEndSeconds, setTrimEndSeconds] = useState(0);
  const [vocalGainDb, setVocalGainDb] = useState(0);
  const [instrumentalGainDb, setInstrumentalGainDb] = useState(-3);
  const [validationError, setValidationError] = useState("");

  const usesUploadedInstrumental = selectedInstrumental === "upload";
  const selectedVocal = vocalSource === "active" ? activeAudio : vocalFile;
  const canCreate = Boolean(
    selectedVocal
    && (usesUploadedInstrumental ? instrumentalFile : selectedInstrumental),
  );

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setValidationError("");
    if (!selectedVocal) {
      setValidationError("Choose or record the isolated vocal you want to add.");
      return;
    }
    if (usesUploadedInstrumental && !instrumentalFile) {
      setValidationError("Choose an instrumental or select one of your completed results.");
      return;
    }
    if (trimEndSeconds > 0 && trimEndSeconds <= trimStartSeconds) {
      setValidationError("Vocal trim end must be later than vocal trim start.");
      return;
    }
    onCreate({
      ...(usesUploadedInstrumental
        ? { instrumentalFile: instrumentalFile ?? undefined }
        : { instrumentalJobId: selectedInstrumental }),
      vocalFile: selectedVocal,
      offsetMs,
      trimStartSeconds,
      trimEndSeconds,
      vocalGainDb,
      instrumentalGainDb,
    });
  }

  const absoluteUrl = (path: string) => new URL(path, `${processorUrl}/`).toString();

  return (
    <section className="workflow-card mixer-card" aria-labelledby="mixer-heading">
      <div className="step-heading">
        <span className="step-number"><SlidersHorizontal size={15} /></span>
        <div>
          <h3 id="mixer-heading">Mix your vocal with an instrumental</h3>
          <p>Use a completed no-vocals track or upload another backing track, then place and balance your isolated vocal.</p>
        </div>
      </div>

      <form className="mixer-form mt-5" onSubmit={submit}>
        <fieldset className="mixer-fieldset">
          <legend>1 · Instrumental</legend>
          <label className="mixer-label" htmlFor="mixer-instrumental">Backing track</label>
          <select
            disabled={busy}
            id="mixer-instrumental"
            onChange={(event) => onInstrumentalChange(event.target.value)}
            value={selectedInstrumental}
          >
            <option value="upload">Upload another instrumental</option>
            {instrumentalOptions.map((option) => <option key={option.id} value={option.id}>{option.label}</option>)}
          </select>
          {usesUploadedInstrumental && (
            <label className="mixer-file-picker">
              <Music2 size={17} />
              <span>{instrumentalFile?.name || "Choose instrumental audio"}</span>
              <input
                accept={MIXER_AUDIO_ACCEPT}
                disabled={busy}
                onChange={(event) => chooseAudioFile(event, setInstrumentalFile, setValidationError)}
                onClick={(event) => { event.currentTarget.value = ""; }}
                type="file"
              />
            </label>
          )}
        </fieldset>

        <fieldset className="mixer-fieldset">
          <legend>2 · Your vocal</legend>
          <div className="mixer-source-toggle" role="group" aria-label="Vocal source">
            <button
              aria-pressed={vocalSource === "upload"}
              className={vocalSource === "upload" ? "is-active" : ""}
              disabled={busy}
              onClick={() => setVocalSource("upload")}
              type="button"
            >
              <Mic2 size={16} />Upload vocal
            </button>
            <button
              aria-pressed={vocalSource === "active"}
              className={vocalSource === "active" ? "is-active" : ""}
              disabled={busy || !activeAudio}
              onClick={() => setVocalSource("active")}
              type="button"
            >
              <FileAudio size={16} />Use active audio
            </button>
          </div>
          {vocalSource === "upload" ? (
            <label className="mixer-file-picker">
              <Mic2 size={17} />
              <span>{vocalFile?.name || "Choose isolated vocal audio"}</span>
              <input
                accept={MIXER_AUDIO_ACCEPT}
                disabled={busy}
                onChange={(event) => chooseAudioFile(event, setVocalFile, setValidationError)}
                onClick={(event) => { event.currentTarget.value = ""; }}
                type="file"
              />
            </label>
          ) : (
            <p className="mixer-active-file"><FileAudio size={15} />{activeAudio?.name || "Add or record audio above first"}</p>
          )}
        </fieldset>

        <fieldset className="mixer-fieldset">
          <legend>3 · Timing and levels</legend>
          <div className="mixer-number-grid">
            <label className="mixer-label">
              Vocal offset <span>milliseconds</span>
              <input disabled={busy} max={30000} min={-10000} onChange={(event) => setOffsetMs(Number(event.target.value))} step={10} type="number" value={offsetMs} />
              <small>Positive starts the vocal later; negative skips its beginning.</small>
            </label>
            <label className="mixer-label">
              Trim vocal start <span>seconds</span>
              <input disabled={busy} min={0} onChange={(event) => setTrimStartSeconds(Math.max(0, Number(event.target.value)))} step={0.1} type="number" value={trimStartSeconds} />
            </label>
            <label className="mixer-label">
              Trim vocal end <span>seconds · 0 keeps all</span>
              <input disabled={busy} min={0} onChange={(event) => setTrimEndSeconds(Math.max(0, Number(event.target.value)))} step={0.1} type="number" value={trimEndSeconds} />
            </label>
          </div>
          <div className="mixer-slider-grid">
            <label className="mixer-range-label">
              <span>Vocal level <strong>{vocalGainDb > 0 ? "+" : ""}{vocalGainDb} dB</strong></span>
              <input disabled={busy} max={12} min={-24} onChange={(event) => setVocalGainDb(Number(event.target.value))} step={0.5} type="range" value={vocalGainDb} />
            </label>
            <label className="mixer-range-label">
              <span>Instrumental level <strong>{instrumentalGainDb > 0 ? "+" : ""}{instrumentalGainDb} dB</strong></span>
              <input disabled={busy} max={6} min={-24} onChange={(event) => setInstrumentalGainDb(Number(event.target.value))} step={0.5} type="range" value={instrumentalGainDb} />
            </label>
          </div>
        </fieldset>

        {validationError && <p className="mixer-error" role="alert">{validationError}</p>}
        <button className="button-primary w-full justify-center" disabled={busy || !configured || !canCreate} type="submit">
          {busy ? <LoaderCircle className="animate-spin" size={18} /> : <WandSparkles size={18} />}
          {busy ? "Creating your mix…" : "Create vocal mix"}
        </button>
      </form>

      {result && (
        <div className="mixer-result mt-5">
          <div>
            <p className="mixer-result-title">Your mix is ready</p>
            <p>Preview it here, then save WAV or MP3—or send the MP3 through WhatsApp.</p>
          </div>
          <audio aria-label="Vocal mix preview" controls preload="metadata" src={absoluteUrl(result.mp3_url)} />
          <ExportActions
            compact
            fileName="stem-studio-vocal-mix.wav"
            mimeType="audio/wav"
            remoteUrl={absoluteUrl(result.wav_url)}
            saveLabel="Save WAV"
            secondaryFileName="stem-studio-vocal-mix.mp3"
            secondaryMimeType="audio/mpeg"
            secondaryRemoteUrl={absoluteUrl(result.mp3_url)}
            secondarySaveLabel="Save MP3"
            shareFileName="stem-studio-vocal-mix.mp3"
            shareMimeType="audio/mpeg"
            shareRemoteUrl={absoluteUrl(result.mp3_url)}
            showHint
          />
          <p className="mixer-expiry">Temporary result · save it within {Math.max(1, Math.ceil(result.expires_in_seconds / 60))} minutes.</p>
        </div>
      )}
    </section>
  );
}
