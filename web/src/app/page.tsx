import { Cloud, Headphones, LockKeyhole, Music2, ServerCog } from "lucide-react";

import { AuthButton } from "@/components/auth-button";
import { Studio } from "@/components/studio";
import { createClient } from "@/lib/supabase/server";
import type { SeparationJob } from "@/lib/types";

export const dynamic = "force-dynamic";

function SetupRequired() {
  return (
    <main className="shell grid min-h-screen place-items-center px-6 py-16">
      <section className="glass max-w-2xl rounded-[2rem] p-10 text-center">
        <div className="brand-mark mx-auto mb-6"><Music2 size={28} /></div>
        <p className="eyebrow">Cloud setup pending</p>
        <h1 className="mt-3 text-4xl font-semibold tracking-[-0.04em] text-white">Stem Studio is ready to connect.</h1>
        <p className="mx-auto mt-5 max-w-lg text-pretty text-base leading-7 text-slate-300">
          Add the Supabase project URL and publishable key to enable Google sign-in, private uploads, and separation jobs.
        </p>
      </section>
    </main>
  );
}

export default async function Home() {
  if (!process.env.NEXT_PUBLIC_SUPABASE_URL || !process.env.NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY) {
    return <SetupRequired />;
  }

  const supabase = await createClient();
  const { data: claimsData } = await supabase.auth.getClaims();
  const userId = claimsData?.claims?.sub;
  let jobs: SeparationJob[] = [];

  if (userId) {
    const { data } = await supabase
      .from("separation_jobs")
      .select("*")
      .order("created_at", { ascending: false })
      .limit(12);
    jobs = (data ?? []) as SeparationJob[];
  }

  return (
    <main className="shell min-h-screen px-5 py-6 sm:px-8 lg:px-12">
      <header className="mx-auto flex max-w-6xl items-center justify-between py-2">
        <div className="flex items-center gap-3">
          <div className="brand-mark"><Music2 size={22} /></div>
          <div>
            <p className="text-base font-semibold tracking-tight text-white">Stem Studio</p>
            <p className="text-xs text-slate-400">Private audio workspace</p>
          </div>
        </div>
        <AuthButton signedIn={Boolean(userId)} />
      </header>

      <section className="mx-auto max-w-6xl pb-20 pt-16 sm:pt-24">
        <div className="max-w-3xl">
          <div className="status-pill"><span className="status-dot" /> Local-quality separation, cloud convenience</div>
          <h1 className="mt-7 text-balance text-5xl font-semibold tracking-[-0.055em] text-white sm:text-7xl">
            Pull the song apart.<br /><span className="gradient-text">Put your voice in.</span>
          </h1>
          <p className="mt-7 max-w-2xl text-pretty text-lg leading-8 text-slate-300">
            Create no-vocal tracks for singing, isolate drums for guitar practice, and keep every upload private to your account.
          </p>
        </div>

        <div className="mt-12 grid gap-4 sm:grid-cols-3">
          {[
            [Headphones, "Three useful stems", "Instrumental, drums, and vocals from one upload."],
            [LockKeyhole, "Private by default", "Files are protected by per-user storage policies."],
            [ServerCog, "Background processing", "Close the tab; your job state remains in the cloud."],
          ].map(([Icon, title, text]) => {
            const FeatureIcon = Icon as typeof Headphones;
            return (
              <article className="feature-card" key={title as string}>
                <FeatureIcon size={20} className="text-violet-300" />
                <h2 className="mt-4 font-medium text-white">{title as string}</h2>
                <p className="mt-2 text-sm leading-6 text-slate-400">{text as string}</p>
              </article>
            );
          })}
        </div>

        {userId ? (
          <Studio initialJobs={jobs} userId={userId} />
        ) : (
          <section className="glass mt-10 overflow-hidden rounded-[2rem] p-8 sm:p-12">
            <div className="grid items-center gap-10 lg:grid-cols-[1.2fr_.8fr]">
              <div>
                <p className="eyebrow">Your studio, anywhere</p>
                <h2 className="mt-3 text-3xl font-semibold tracking-[-0.035em] text-white">Sign in to start a private session.</h2>
                <p className="mt-4 max-w-xl leading-7 text-slate-300">Google sign-in creates your own locked workspace for uploads, stems, and progress.</p>
                <div className="mt-7"><AuthButton signedIn={false} prominent /></div>
              </div>
              <div className="cloud-orbit" aria-hidden="true"><Cloud size={54} /></div>
            </div>
          </section>
        )}
      </section>
    </main>
  );
}
