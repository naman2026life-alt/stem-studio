import { createHmac, timingSafeEqual } from "node:crypto";

const MAX_AUTH_BODY_BYTES = 4096;
const FAILURE_WINDOW_MS = 60_000;
const MAX_FAILURES = 8;
const MAX_FAILURE_KEYS = 512;
// Best-effort throttling within one warm process, not a distributed rate limit.
// Vercel instances and cold starts do not share this state.
const failures = new Map<string, { count: number; expiresAt: number }>();

function json(body: Record<string, unknown>, status = 200, headers: Record<string, string> = {}) {
  return Response.json(body, { status, headers: { "Cache-Control": "no-store", ...headers } });
}

function matches(left: string, right: string) {
  const leftBuffer = Buffer.from(left);
  const rightBuffer = Buffer.from(right);
  return leftBuffer.length === rightBuffer.length && timingSafeEqual(leftBuffer, rightBuffer);
}

function isLocalStudioRequest(request: Request) {
  const host = request.headers.get("host")?.toLowerCase();
  // Binding to loopback is not sufficient: an untrusted site's DNS can resolve
  // to loopback. Accept only the local UI's literal host and its own requests.
  if (host !== "127.0.0.1:3007" && host !== "localhost:3007") return false;
  if (request.headers.get("origin") !== `http://${host}`) return false;
  const site = request.headers.get("sec-fetch-site");
  if (site && site !== "same-origin") return false;
  return request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() === "application/json";
}

function failureKey(request: Request) {
  // Vercel overwrites this header at its edge. Outside Vercel, do not trust a
  // caller-supplied IP header; use the shared bounded fallback bucket instead.
  return process.env.VERCEL === "1"
    ? (request.headers.get("x-forwarded-for")?.split(",", 1)[0].trim().slice(0, 64) || "unknown")
    : "standalone";
}

function recentFailures(key: string) {
  const current = failures.get(key);
  if (current && current.expiresAt <= Date.now()) { failures.delete(key); return undefined; }
  return current;
}

function recordFailure(key: string) {
  const current = recentFailures(key);
  if (current) { current.count += 1; return; }
  if (failures.size >= MAX_FAILURE_KEYS) {
    for (const [oldKey, record] of failures) if (record.expiresAt <= Date.now()) failures.delete(oldKey);
    const oldest = failures.keys().next().value;
    if (failures.size >= MAX_FAILURE_KEYS && oldest !== undefined) failures.delete(oldest);
  }
  failures.set(key, { count: 1, expiresAt: Date.now() + FAILURE_WINDOW_MS });
}

async function passwordBody(request: Request): Promise<{ password?: unknown } | Response> {
  if (request.headers.get("content-type")?.split(";", 1)[0].trim().toLowerCase() !== "application/json") {
    return json({ error: "Send the studio password as JSON." }, 415);
  }
  if (Number(request.headers.get("content-length")) > MAX_AUTH_BODY_BYTES) return json({ error: "Password request is too large." }, 413);
  const reader = request.body?.getReader();
  if (!reader) return {};
  let size = 0;
  const chunks: Uint8Array[] = [];
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > MAX_AUTH_BODY_BYTES) { await reader.cancel(); return json({ error: "Password request is too large." }, 413); }
      chunks.push(value);
    }
    const bytes = new Uint8Array(size);
    let offset = 0;
    for (const chunk of chunks) { bytes.set(chunk, offset); offset += chunk.byteLength; }
    const parsed = JSON.parse(new TextDecoder().decode(bytes)) as unknown;
    return parsed !== null && typeof parsed === "object" && !Array.isArray(parsed) ? parsed : {};
  } catch { return json({ error: "Enter the studio password and try again." }, 400); }
  finally { reader.releaseLock(); }
}

export async function POST(request: Request) {
  const localOnly = process.env.STEM_STUDIO_LOCAL_WEB === "1" && process.env.VERCEL !== "1";
  const production = process.env.NODE_ENV === "production" || process.env.VERCEL === "1";
  if (localOnly && !isLocalStudioRequest(request)) {
    return json({ error: "Open Stem Studio directly on this Mac to use local processing." }, 403);
  }
  const requiredPassword = process.env.STEM_STUDIO_PASSWORD;
  const secret = process.env.PROCESSOR_SHARED_SECRET;
  if (production && ((!localOnly && !requiredPassword?.trim()) || !secret || secret.length < 32)) {
    return json({ error: "Private processing is not configured. The studio owner needs to reconnect it." }, 503);
  }
  if (requiredPassword) {
    const key = failureKey(request);
    const recent = recentFailures(key);
    if (recent && recent.count >= MAX_FAILURES) {
      return json({ error: "Too many password attempts. Wait a minute, then try again." }, 429, { "Retry-After": String(Math.max(1, Math.ceil((recent.expiresAt - Date.now()) / 1000))) });
    }
    const body = await passwordBody(request);
    if (body instanceof Response) { recordFailure(key); return body; }
    if (typeof body.password !== "string" || !body.password || !matches(body.password, requiredPassword)) {
      recordFailure(key);
      return json({ error: "The studio password is incorrect." }, 401);
    }
    failures.delete(key);
  }

  if (!secret) return json({});

  const timestamp = Math.floor(Date.now() / 1000).toString();
  const signature = createHmac("sha256", secret).update(`${timestamp}:upload`).digest("hex");
  return json({ timestamp, signature });
}
