# Stem Studio web

The Vercel-ready Next.js interface for Stem Studio. It accepts audio or video, optionally extracts video audio to MP3, builds an ordered trim-and-merge edit, uploads the active audio to the temporary processor API, polls isolation progress, and previews or downloads all results. See the repository root README for local setup, processor deployment, environment variables, and architecture notes.

```bash
cp .env.example .env.local
npm install
npm run dev
```
