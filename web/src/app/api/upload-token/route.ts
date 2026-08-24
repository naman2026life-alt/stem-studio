import { createHmac } from "node:crypto";

export async function POST() {
  const secret = process.env.PROCESSOR_SHARED_SECRET;
  if (!secret) return Response.json({});

  const timestamp = Math.floor(Date.now() / 1000).toString();
  const signature = createHmac("sha256", secret).update(`${timestamp}:upload`).digest("hex");
  return Response.json({ timestamp, signature });
}
