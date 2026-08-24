import { createHmac, timingSafeEqual } from "node:crypto";

function matches(left: string, right: string) {
  const leftBuffer = Buffer.from(left);
  const rightBuffer = Buffer.from(right);
  return leftBuffer.length === rightBuffer.length && timingSafeEqual(leftBuffer, rightBuffer);
}

export async function POST(request: Request) {
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
  return Response.json({ timestamp, signature });
}
