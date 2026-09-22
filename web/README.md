# Stem Studio web

The Vercel-ready Next.js interface for Stem Studio. It accepts audio or video, optionally extracts video audio to MP3, builds an ordered trim-and-merge edit, polls temporary isolation jobs, and mixes a recorded vocal with a completed or uploaded instrumental. WAV and MP3 results can be previewed, saved, or shared from mobile. See the repository root README for local setup, processor deployment, environment variables, and architecture notes.

```bash
cp .env.example .env.local
npm install
npm run dev
```

For the private Mac processor, set `NEXT_PUBLIC_PROCESSOR_URL` to its HTTPS URL and `STEM_STUDIO_PROCESSOR_LOCATION=mac`. Keep `PROCESSOR_SHARED_SECRET` matched to the processor and `STEM_STUDIO_PASSWORD` set on Vercel. These changes take effect in the next deployment.

The interface checks whether the Mac is reachable before starting processing. While the Mac sleeps or loses its connection, users can still choose files, record, preview, and save recordings locally. The connection banner offers Retry and checks again automatically while the page is open. Uploaded job IDs remain available for reconnecting; a tunnel error never clears them. Keep the page open to retain recordings that have not been saved.

Hosted production fails closed: `/api/upload-token` requires a configured studio password and a processor signing secret of at least 32 characters. All processor actions use the shared password dialog and signed request headers. The configured password and signing key stay on the server; a password entered by the user is held only in that tab’s memory and is never written to browser storage. The explicitly enabled loopback UI keeps its local-only exception, with Host/Origin checks; this exception cannot activate on Vercel.

Wrong password attempts receive basic throttling (eight failures per minute within one warm server process, with bounded memory). On Vercel, the bucket uses the client address supplied by Vercel’s edge. This is not a distributed limit: cold starts and separate serverless instances have separate state. It supplements the password requirement and is not a substitute for platform-level abuse protection.
