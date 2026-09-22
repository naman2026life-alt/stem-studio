# Private Mac processing

Run the full editor and Singing coach on your Mac without sending processing jobs to Modal. Audio conversion, trim/merge, karaoke, stem separation, permitted YouTube imports, and pitch analysis use the Mac's CPU and local storage. No Modal card or compute credits are required for this mode; it still uses your own electricity, network connection, and disk space. Separation can take materially longer than on a cloud GPU.

**Local readiness is not a remote deployment.** These services alone do not change the existing Vercel website. Phone access needs a separately configured HTTPS tunnel and matching Vercel settings. No tunnel is installed or authorized by the service installers. The recommended Tailscale setup remains a separate consent/setup step.

## Architecture and privacy

| Component | Address or location | Purpose |
| --- | --- | --- |
| Mac interface | `http://127.0.0.1:3007` | Full Next.js app; available only on this Mac |
| Mac processor | `http://127.0.0.1:8766` | Authenticated API; one process and bounded CPU threads |
| Shared signing credential | Login Keychain, service `Stem Studio Processor`, account = your macOS username | Loaded into process memory at startup; never stored in the launch plist |
| Temporary recordings/results | `~/Library/Application Support/Stem Studio/processor` | Durable job metadata; completed jobs survive a service restart until expiry |
| Demucs cache | Repository `.model-cache/` | Reused model downloads; not committed to Git |
| Local web build | `web/.next-mac/` | Separate from the normal hosted build |

The local interface deliberately does not require the website's studio password because it only listens on loopback. **Never expose or tunnel port 3007.** Its signing endpoint is trusted locally. Only expose the authenticated processor on port 8766; keep the public interface on Vercel protected by `STEM_STUDIO_PASSWORD`.

The processor forces authentication even when a tunnel forwards requests from a loopback address. CORS allows the production Vercel origin and local port 3007, not arbitrary websites. Access logging is disabled to avoid writing signed download URLs to logs. One model/media job runs at a time with four CPU threads; YouTube imports have a separate single worker. Queued work is bounded.

Temporary job results expire after one hour; this is not a permanent recording library. Save important recordings and exports to your device. An interrupted job is marked failed after restart, rather than being reported as still running forever. The original file may need uploading again.

## One-time setup

Run from the repository root, as your regular macOS user—not with `sudo`. Reuse an existing working `.venv` if available.

```sh
brew install ffmpeg deno python@3.11 node
"$(brew --prefix python@3.11)/bin/python3.11" -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-processor.txt
npm ci --prefix web
```

Create or verify the generic-password entry in your **login Keychain**:

- Service/item name: `Stem Studio Processor`
- Account: your macOS username (`id -un` shows it)
- Password: the existing `PROCESSOR_SHARED_SECRET`, at least 32 characters, exactly matching Vercel if reusing the hosted interface

Use Keychain Access or a trusted secret-management workflow; do not paste the secret into shell arguments, committed files, browser JavaScript, screenshots, or a `NEXT_PUBLIC_*` setting. The shared signing credential is different from the human-facing studio password. Do not generate a different credential for the Mac unless you also deliberately rotate the corresponding Vercel setting.

Then build and install the two login services:

```sh
source .venv/bin/activate
python -m stem_studio.mac_service --check
python -m stem_studio.mac_web build
zsh scripts/install_macos_processor.sh install
zsh scripts/install_macos_web.sh install
```

Open <http://127.0.0.1:3007> on the Mac. The `--check` step reads Keychain and checks storage paths without starting the API or downloading models. The first stem separation may download the Demucs model. The installers do not install a tunnel, change sleep settings, enable SSH, or alter Remote Desktop settings.

Both services start at login and restart after a crash. They are user LaunchAgents, not before-login system daemons: after a reboot or FileVault unlock, log into the Mac. Keep it awake while processing. Closing the browser does not stop a submitted job, but sleeping or shutting down the Mac interrupts availability.

**macOS Documents privacy:** a checkout inside `Documents` can run successfully from an authorized terminal but be blocked when launched in the background. The denial can occur before Python starts, so an empty log is not proof of a healthy service. Verify readiness after installation before relying on automatic startup. Resolving this may require an explicit macOS privacy approval for the relevant executable, or a user-approved move outside protected folders. Do not disable macOS protections or grant broad disk access as a workaround.

If background startup is blocked, stop the login services and use two foreground terminals temporarily:

```sh
# Terminal 1, repository root:
.venv/bin/python -m stem_studio.mac_service

# Terminal 2, repository root, after building the local interface:
.venv/bin/python -m stem_studio.mac_web start
```

Keep those terminals open. Foreground availability does not establish reboot/login recovery; that remains unverified until the background services pass their readiness checks.

## Start, stop, inspect, and uninstall

```sh
zsh scripts/install_macos_processor.sh status
zsh scripts/install_macos_web.sh status
curl --fail http://127.0.0.1:8766/ready

# Stop both; they stay disabled at later logins until explicitly started.
zsh scripts/install_macos_web.sh stop
zsh scripts/install_macos_processor.sh stop

# Start again.
zsh scripts/install_macos_processor.sh start
zsh scripts/install_macos_web.sh start
```

Use `restart` instead of `start` to restart a running service; finish or save current work first. The processor gets a graceful shutdown window, after which launchd also cleans up subprocesses. For a code update, rebuild the local interface with `python -m stem_studio.mac_web build`, then restart the appropriate services.

```sh
zsh scripts/install_macos_web.sh uninstall
zsh scripts/install_macos_processor.sh uninstall
```

Uninstall removes the services from automatic startup but preserves recordings, models, logs, and Keychain credentials. The processor plist is moved to a recoverable backup under `~/Library/Application Support/Stem Studio/service-backups/`. A separately configured tunnel is not removed by these scripts.

Processor logs are `~/Library/Logs/StemStudioProcessor.log` and `~/Library/Logs/StemStudioProcessor.error.log`. The web service's `status` command identifies its own log paths. Never post credentials or signed playback links when sharing diagnostics.

### Verify the local workflows

With the processor running and idle, run:

```sh
.venv/bin/python scripts/verify_mac.py
```

This opt-in smoke test uses real models and generated audio/video: pitch/reference alignment, silence rejection, vocal isolation, stem reuse, trim/clamp/merge, video-to-MP3, vocal mixing, downloads, authentication, CORS, and partial audio playback. It reads the existing Keychain entry without printing it, connects only to `127.0.0.1:8766`, and does not call Modal or change services. Test results expire through the normal one-hour cleanup. Synthetic success checks integration and known signals, not separation quality on every real song.

## Phone access through Vercel: separate cutover

The intended path is **iPhone → existing Vercel interface → authenticated HTTPS tunnel → Mac processor**. Media uploads go directly to the processor, not through a Vercel function. The browser submits work and polls its job ID; it does not need one HTTP request to stay open for an entire analysis.

Tailscale Personal does [not require billing details](https://tailscale.com/docs/account/billing/modify-billing), and [Funnel](https://tailscale.com/docs/features/tailscale-funnel) can publish an HTTPS endpoint that visitors reach without installing Tailscale on their phones. Funnel is public and currently beta, with non-configurable bandwidth limits; it does not replace the app's authentication. Account sign-in, daemon installation, and enabling public Funnel need their own setup/approval. Persistent daemon state and background forwarding are needed to keep the hostname stable across restarts. Do not promise phone availability until uploads, playback, and restart recovery have been tested.

Once the tunnel exists, configure the existing Vercel project:

```text
NEXT_PUBLIC_PROCESSOR_URL=https://YOUR-STABLE-PROCESSOR-HOSTNAME
PROCESSOR_SHARED_SECRET=UNCHANGED-MATCHING-KEYCHAIN-VALUE
STEM_STUDIO_PASSWORD=YOUR-EXISTING-PRIVATE-STUDIO-PASSWORD
STEM_STUDIO_PROCESSOR_LOCATION=mac
```

Redeploy: `NEXT_PUBLIC_PROCESSOR_URL` is embedded at build time. Retain the same signing secret and studio password unless intentionally rotating them. The `mac` location setting labels connectivity failures appropriately; it does not configure a tunnel or move any processing by itself.

Before calling the remote switch complete, test a phone upload, recording, trim, separation, and short pitch analysis; confirm audio playback/download; test a full 150 MB upload and daemon restart recovery. Keep the Mac awake, logged in, online, and running both its processor and tunnel. If unavailable, the interface can still record, preview, and save locally; processing waits until connectivity is restored. Save unsent recordings before reloading a phone page.

Starting Mac services does not stop old Modal deployments or an existing Modal-backed YouTube helper. Only after verifying the new route, review and stop any now-unneeded cloud workers/helpers separately to avoid continued polling or compute use. Retain the original Modal configuration as a rollback reference if desired; the Mac installers never modify it.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Keychain unavailable / processor repeatedly restarts | Unlock the login Keychain. Verify the exact item name, current macOS account, and a matching 32+ character secret. Run `python -m stem_studio.mac_service --check`, then restart. Never work around this by disabling authentication. |
| Local page will not open | Run both `status` commands; verify the local web build completed. Use `127.0.0.1:3007`, not the original Gradio port 7860. Check whether another process owns the configured port. |
| Service process exists but readiness fails and logs are empty | A project under `Documents` may be blocked by macOS privacy before Python starts. Use the foreground fallback above; request the appropriate user-approved privacy fix before claiming automatic startup works. |
| Local API readiness fails | Inspect the processor error log, dependencies, available disk space, FFmpeg, and permissions on its data/cache directories. `/ready` checks prerequisites without running a neural model. |
| Phone says Mac offline, but local page works | Confirm the tunnel is running, the Mac is awake, and the deployed processor URL uses HTTPS—not `127.0.0.1`, which would point at the phone itself. A Vercel redeployment is required after a URL change. |
| Permission / signature / CORS error | Verify Vercel and Keychain share the same signing credential and the request comes from the allowed production origin. A Vercel preview domain is not automatically trusted. |
| First analysis is slow | Model loading/downloads and CPU processing take time. Start with a short, clear solo-vocal phrase; inspect job progress before resubmitting. |
| Job failed after restart | In-flight computation is not resumed. Re-submit the source; completed, unexpired outputs should remain retrievable. |
| YouTube import is rejected | The Mac uses its own connection, but YouTube restrictions can still apply. No browser cookies or Google credentials are imported. Upload an authorized audio file directly instead. |

For the original local development commands, Gradio, Docker, and optional Modal deployment, return to the [main README](../README.md).
