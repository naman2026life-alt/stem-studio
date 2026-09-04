"use client";

import { RotateCcw, TriangleAlert } from "lucide-react";
import { useEffect } from "react";

export default function ErrorPage({ error, retry }: { error: Error & { digest?: string }; retry: () => void }) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <main className="shell error-shell">
      <section className="glass error-card" role="alert">
        <span className="error-icon"><TriangleAlert size={24} /></span>
        <p className="eyebrow">Stem Studio</p>
        <h1>Something interrupted the workspace.</h1>
        <p>Your audio has not been uploaded by this screen. Try reopening the workspace; temporary server results can reconnect when their job IDs are still available.</p>
        <button className="button-primary" onClick={retry} type="button"><RotateCcw size={17} />Try again</button>
      </section>
    </main>
  );
}
