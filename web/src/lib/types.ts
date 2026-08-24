export type SeparationJob = {
  id: string;
  user_id: string;
  status: "queued" | "processing" | "completed" | "failed";
  source_path: string;
  source_name: string;
  vocals_path: string | null;
  drums_path: string | null;
  instrumental_path: string | null;
  progress: number;
  error: string | null;
  created_at: string;
  updated_at: string;
};
