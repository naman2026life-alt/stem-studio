"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CheckCircle2, Laptop, LoaderCircle, RefreshCw, WifiOff } from "lucide-react";

import { isLoopbackProcessor, processorConnectionStatus, processorNeedsAttentionMessage, processorUnavailableMessage, type ProcessorLocation, type ProcessorStatus } from "@/lib/processor-connection";

export function useProcessorConnection(processorUrl: string, location: ProcessorLocation) {
  const [status, setStatus] = useState<ProcessorStatus>(processorUrl ? "checking" : "unconfigured");
  const [checking, setChecking] = useState(false);
  const requestRef = useRef<Promise<boolean> | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(false);
  const lastReadyRef = useRef(0);
  const lastStatusRef = useRef<ProcessorStatus>("checking");
  const local = isLoopbackProcessor(processorUrl);

  const check = useCallback(async (): Promise<boolean> => {
    if (!processorUrl) return false;
    if (requestRef.current) return requestRef.current;
    const controller = new AbortController();
    abortRef.current = controller;
    if (mountedRef.current) setChecking(true);
    const timeout = window.setTimeout(() => controller.abort(), 8_000);
    const request = Promise.resolve().then(async () => {
      try {
        if (!local && !navigator.onLine) throw new TypeError("Network unavailable");
        const response = await fetch(`${processorUrl}/${location === "mac" ? "ready" : "health"}`, { cache: "no-store", signal: controller.signal, headers: { Accept: "application/json" } });
        const reportedStatus = processorConnectionStatus(await response.json().catch(() => null), location);
        const healthy = response.ok && reportedStatus === "ready";
        const nextStatus = healthy ? "ready" : reportedStatus === "attention" ? "attention" : "unavailable";
        lastStatusRef.current = nextStatus;
        if (healthy) lastReadyRef.current = Date.now();
        else lastReadyRef.current = 0;
        if (mountedRef.current) setStatus(nextStatus);
        return healthy;
      } catch {
        lastReadyRef.current = 0;
        lastStatusRef.current = "unavailable";
        if (mountedRef.current) setStatus("unavailable");
        return false;
      } finally {
        window.clearTimeout(timeout);
        if (mountedRef.current) setChecking(false);
        requestRef.current = null;
        abortRef.current = null;
      }
    });
    requestRef.current = request;
    return request;
  }, [local, location, processorUrl]);

  const requireReady = useCallback(async () => {
    if (!processorUrl) throw new Error("Audio processing is not connected yet. You can still record, preview, and save audio on this device.");
    if ((local || navigator.onLine) && lastReadyRef.current > Date.now() - 15_000) return;
    if (!await check()) throw new Error(lastStatusRef.current === "attention" ? processorNeedsAttentionMessage() : processorUnavailableMessage(location));
  }, [check, local, location, processorUrl]);

  useEffect(() => {
    mountedRef.current = true;
    const initial = window.setTimeout(() => void check(), 0);
    // Cloud services can scale to zero. Only poll a personal Mac continuously.
    const interval = location === "mac" ? window.setInterval(() => { if (!document.hidden) void check(); }, 30_000) : null;
    const onVisible = () => { if (!document.hidden) void check(); };
    const onOffline = () => { if (local) void check(); else { lastReadyRef.current = 0; setStatus("unavailable"); } };
    const onOnline = () => void check();
    document.addEventListener("visibilitychange", onVisible);
    window.addEventListener("online", onOnline);
    window.addEventListener("offline", onOffline);
    return () => {
      mountedRef.current = false;
      window.clearTimeout(initial);
      if (interval !== null) window.clearInterval(interval);
      abortRef.current?.abort();
      document.removeEventListener("visibilitychange", onVisible);
      window.removeEventListener("online", onOnline);
      window.removeEventListener("offline", onOffline);
    };
  }, [check, local, location]);

  return { status, checking, check, requireReady, local };
}

export function ProcessorConnection({ location, status, checking, onRetry, local = false }: {
  location: ProcessorLocation;
  status: ProcessorStatus;
  checking: boolean;
  onRetry: () => void;
  local?: boolean;
}) {
  const isMac = location === "mac";
  if (status === "ready" && !isMac) return null;
  const offline = status === "unavailable";
  const missing = status === "unconfigured";
  const attention = status === "attention";
  return <div className={`processor-connection is-${status}`} role="status" aria-live="polite">
    <span className="processor-connection-icon">{status === "checking" ? <LoaderCircle size={18} className="animate-spin" /> : offline ? <WifiOff size={18} /> : status === "ready" ? <CheckCircle2 size={18} /> : <Laptop size={18} />}</span>
    <div><strong>{status === "ready" ? "Your Mac is connected" : status === "checking" ? isMac ? "Connecting to your Mac…" : "Connecting to audio processing…" : missing ? "Audio processing is not connected yet" : attention ? "Your Mac’s audio processor needs attention" : local ? "The audio processor on this Mac isn’t running" : isMac ? "Your Mac isn’t reachable right now" : "Audio processing is temporarily unavailable"}</strong>
      <p>{status === "ready" ? local ? "Audio processing is running on this Mac. You can record, preview, and save here." : "Keep it awake and online while processing. Recording and playback stay on the device you’re using." : status === "checking" ? "You can choose or record your audio while we check." : missing ? "You can still record, preview, and save audio on this device." : attention ? "Check Stem Studio on your Mac, then retry when it’s ready. Your selected audio is still here." : local ? "Start the Stem Studio processor on this Mac, then retry. You can still record, preview, and save your take here." : isMac ? "Wake your Mac and check its internet connection. You can still record, preview, and save on this device. Keep this page open to keep your take." : "Check your internet connection and retry. Your selected audio stays on this page."}</p>
    </div>
    {!missing && status !== "ready" && <button disabled={checking} onClick={onRetry} type="button">{checking ? <LoaderCircle size={15} className="animate-spin" /> : <RefreshCw size={15} />}{checking ? "Checking" : "Retry"}</button>}
  </div>;
}
