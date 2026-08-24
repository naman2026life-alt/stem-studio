import { Film, Music2, Scissors, WandSparkles } from "lucide-react";

import { Studio } from "@/components/studio";

export default function Home() {
  const processorUrl = process.env.NEXT_PUBLIC_PROCESSOR_URL?.replace(/\/$/, "") ?? "";
  const accessProtected = Boolean(process.env.STEM_STUDIO_PASSWORD);

  return (
    <main className="shell min-h-screen px-5 py-6 sm:px-8 lg:px-12">
      <header className="mx-auto flex max-w-6xl items-center justify-between py-2">
        <div className="flex items-center gap-3">
          <div className="brand-mark"><Music2 size={22} /></div>
          <div>
            <p className="text-base font-semibold tracking-tight text-white">Stem Studio</p>
            <p className="text-xs text-slate-400">Temporary audio workspace</p>
          </div>
        </div>
        <span className="status-pill"><span className="status-dot" /> No account needed</span>
      </header>

      <section className="mx-auto max-w-6xl pb-20 pt-16 sm:pt-24">
        <div className="max-w-3xl">
          <p className="eyebrow">Built for practice and performance</p>
          <h1 className="mt-7 text-balance text-5xl font-semibold tracking-[-0.055em] text-white sm:text-7xl">
            Pull the song apart.<br /><span className="gradient-text">Keep what you need.</span>
          </h1>
          <p className="mt-7 max-w-2xl text-pretty text-lg leading-8 text-slate-300">
            Turn a video into MP3, keep and join the exact song sections you want, then create no-vocals, drums-only, and vocals-only tracks.
          </p>
        </div>

        <div className="mt-12 grid gap-4 sm:grid-cols-3">
          {[
            [Film, "Audio or video", "Upload common audio formats or extract an MP3 from video."],
            [Scissors, "Trim and merge", "Keep multiple time ranges and join them in the order you choose."],
            [WandSparkles, "Three useful stems", "Instrumental, drums, and vocals from the full or edited audio."],
          ].map(([Icon, title, text]) => {
            const FeatureIcon = Icon as typeof Film;
            return (
              <article className="feature-card" key={title as string}>
                <FeatureIcon size={20} className="text-violet-300" />
                <h2 className="mt-4 font-medium text-white">{title as string}</h2>
                <p className="mt-2 text-sm leading-6 text-slate-400">{text as string}</p>
              </article>
            );
          })}
        </div>

        <Studio accessProtected={accessProtected} processorUrl={processorUrl} />
      </section>
    </main>
  );
}
