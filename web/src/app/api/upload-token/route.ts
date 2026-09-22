import { createHmac, timingSafeEqual } from "node:crypto";

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

export async function POST(request: Request) {
  if (process.env.STEM_STUDIO_LOCAL_WEB === "1" && !isLocalStudioRequest(request)) {
    return Response.json({ error: "Open Stem Studio directly on this Mac to use local processing." }, { status: 403, headers: { "Cache-Control": "no-store" } });
  }
  const requiredPassword = process.env.STEM_STUDIO_PASSWORD;
  if (requiredPassword) {
    const body = await request.json().catch(() => ({})) as { password?: string };
    if (!body.password || !matches(body.password, requiredPassword)) {
      return Response.json({ error: "Unauthorized" }, { status: 401 });
    }
  }

  const secret = process.env.PROCESSOR_SHARED_SECRET;
  if (!secret) return Response.json({});

  const timestamp = Math.floor(Date.now() / 1000).toString();
  const signature = createHmac("sha256", secret).update(`${timestamp}:upload`).digest("hex");
  return Response.json({ timestamp, signature }, { headers: { "Cache-Control": "no-store" } });
}
