import { Music2 } from "lucide-react";

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
            <p className="text-xs text-slate-400">A little closer to your music</p>
          </div>
        </div>
        <span className="status-pill"><span className="status-dot" /> No account needed</span>
      </header>

      <section className="home-content mx-auto max-w-6xl pb-20 pt-9 sm:pt-12">
        <div className="hero-copy max-w-3xl">
          <p className="eyebrow">Built for practice and performance</p>
          <h1 className="mt-4 text-balance text-4xl font-semibold tracking-[-0.05em] text-white sm:text-5xl">
            Your music. <span className="gradient-text">Your voice.</span>
          </h1>
          <p className="mt-4 max-w-2xl text-pretty text-base leading-7 text-slate-400">
            Make the backing track you need, or get to know the notes you sing. One space to create, practise, and find your sound.
          </p>
        </div>

        <Studio accessProtected={accessProtected} processorUrl={processorUrl} />
      </section>
    </main>
  );
}
