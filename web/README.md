# Stem Studio web

The Vercel-ready Next.js interface for Stem Studio. It accepts audio or video, optionally extracts video audio to MP3, builds an ordered trim-and-merge edit, polls temporary isolation jobs, and mixes a recorded vocal with a completed or uploaded instrumental. WAV and MP3 results can be previewed, saved, or shared from mobile. See the repository root README for local setup, processor deployment, environment variables, and architecture notes.

```bash
cp .env.example .env.local
npm install
npm run dev
```
