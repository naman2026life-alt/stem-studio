export type ProcessorLocation = "mac" | "hosted";
export type ProcessorStatus = "checking" | "ready" | "attention" | "unavailable" | "unconfigured";

export function processorUnavailableMessage(location: ProcessorLocation) {
  return location === "mac"
    ? "We can’t reach your Mac right now. Wake it up and check its internet connection, then try again. Keep this page open—your selected audio and recordings are still here."
    : "The audio processor isn’t reachable right now. Check your connection and try again. Keep this page open—your selected audio is still here.";
}

export function processingErrorMessage(error: unknown, fallback: string, location: ProcessorLocation) {
  if (error instanceof TypeError || (error instanceof Error && (/^(failed to fetch|load failed|networkerror|network request failed)/i.test(error.message) || /\((502|503|504|521|522|523|524|530)\)/.test(error.message)))) {
    return processorUnavailableMessage(location);
  }
  return error instanceof Error ? error.message : fallback;
}

export function isHealthyProcessor(body: unknown): boolean {
  return body !== null && typeof body === "object" && "status" in body && body.status === "ok";
}

export function processorConnectionStatus(body: unknown, location: ProcessorLocation): "ready" | "attention" | "unavailable" {
  if (location !== "mac") return isHealthyProcessor(body) ? "ready" : "unavailable";
  if (body === null || typeof body !== "object" || !("status" in body)) return "unavailable";
  if (body.status === "not_ready") return "attention";
  if (body.status !== "ready") return "unavailable";
  if ("checks" in body && body.checks && typeof body.checks === "object" && Object.values(body.checks).some((value) => value !== true)) return "attention";
  return "ready";
}

export function isLoopbackProcessor(url: string): boolean {
  try {
    const parsed = new URL(url);
    return ["http:", "https:"].includes(parsed.protocol) && ["127.0.0.1", "localhost", "[::1]"].includes(parsed.hostname);
  } catch { return false; }
}

export function processorNeedsAttentionMessage() {
  return "Your Mac is connected, but audio processing isn’t ready yet. Check Stem Studio on the Mac, then retry. Your selected audio and recordings are still here.";
}

export async function isMissingProcessorResource(response: Response): Promise<boolean> {
  if (response.status !== 404 && response.status !== 410) return false;
  // A disconnected tunnel can return its own 404 page. Only forget a job when
  // the processor itself identifies the temporary resource as missing.
  const body = await response.clone().json().catch(() => null) as { detail?: unknown } | null;
  return typeof body?.detail === "string" && /expired|could not be found/i.test(body.detail);
}
