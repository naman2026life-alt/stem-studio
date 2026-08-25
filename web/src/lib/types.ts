export type SeparationMode = "stems" | "karaoke";

export type SeparationJob = {
  id: string;
  source_name: string;
  mode?: SeparationMode;
  status: "queued" | "processing" | "completed" | "failed";
  progress: number;
  created_at: string;
  expires_in_seconds: number;
  error: string | null;
  vocals_url: string | null;
  drums_url: string | null;
  instrumental_url: string | null;
};
