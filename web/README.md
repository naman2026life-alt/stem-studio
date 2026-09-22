# Stem Studio web

The Vercel-ready Next.js interface for Stem Studio. It accepts audio or video, optionally extracts video audio to MP3, builds an ordered trim-and-merge edit, polls temporary isolation jobs, and mixes a recorded vocal with a completed or uploaded instrumental. WAV and MP3 results can be previewed, saved, or shared from mobile. See the repository root README for local setup, processor deployment, environment variables, and architecture notes.

```bash
cp .env.example .env.local
npm install
npm run dev
```

For the private Mac processor, set `NEXT_PUBLIC_PROCESSOR_URL` to its HTTPS URL and `STEM_STUDIO_PROCESSOR_LOCATION=mac`. Keep `PROCESSOR_SHARED_SECRET` matched to the processor and `STEM_STUDIO_PASSWORD` set on Vercel. These changes take effect in the next deployment.

The interface checks whether the Mac is reachable before starting processing. While the Mac sleeps or loses its connection, users can still choose files, record, preview, and save recordings locally. The connection banner offers Retry and checks again automatically while the page is open. Uploaded job IDs remain available for reconnecting; a tunnel error never clears them. Keep the page open to retain recordings that have not been saved.
