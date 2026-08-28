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

export type YouTubeImportJob = {
  id: string;
  status: "queued" | "processing" | "completed" | "failed";
  progress: number;
  created_at: string;
  expires_in_seconds: number;
  error: string | null;
  title: string | null;
  file_name: string | null;
  file_url: string | null;
  duration_seconds: number | null;
};
