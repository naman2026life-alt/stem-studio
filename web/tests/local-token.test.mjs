import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { POST } from "../src/app/api/upload-token/route.ts";

let original;
beforeEach(() => {
  original = { local: process.env.STEM_STUDIO_LOCAL_WEB, secret: process.env.PROCESSOR_SHARED_SECRET, password: process.env.STEM_STUDIO_PASSWORD };
  process.env.STEM_STUDIO_LOCAL_WEB = "1";
  process.env.PROCESSOR_SHARED_SECRET = "test-only-secret-that-is-never-a-real-credential";
  process.env.STEM_STUDIO_PASSWORD = "";
});
afterEach(() => {
  for (const [key, value] of [["STEM_STUDIO_LOCAL_WEB", original.local], ["PROCESSOR_SHARED_SECRET", original.secret], ["STEM_STUDIO_PASSWORD", original.password]]) {
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
});

function request(headers = {}, body = "{}") {
  return new Request("http://127.0.0.1:3007/api/upload-token", { method: "POST", headers: { host: "127.0.0.1:3007", origin: "http://127.0.0.1:3007", "content-type": "application/json", "sec-fetch-site": "same-origin", ...headers }, body });
}

test("local UI can obtain a short-lived processing signature from its own origin", async () => {
  const response = await POST(request());
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.match(body.timestamp, /^\d+$/);
  assert.match(body.signature, /^[a-f0-9]{64}$/);
  assert.equal(response.headers.get("cache-control"), "no-store");
});

test("DNS rebinding cannot use the local signing route", async () => {
  const response = await POST(request({ host: "attacker.example:3007", origin: "http://attacker.example:3007" }));
  assert.equal(response.status, 403);
  assert.equal((await response.json()).signature, undefined);
});

test("cross-site, opaque-origin and form requests cannot use local signing", async () => {
  for (const headers of [
    { origin: "https://attacker.example", "sec-fetch-site": "cross-site" },
    { origin: "null" },
    { origin: "" },
    { "sec-fetch-site": "cross-site" },
    { "content-type": "text/plain" },
    { host: "127.0.0.1:3008", origin: "http://127.0.0.1:3008" },
  ]) assert.equal((await POST(request(headers))).status, 403);
});

test("localhost is allowed only with its matching local origin", async () => {
  assert.equal((await POST(request({ host: "localhost:3007", origin: "http://localhost:3007" }))).status, 200);
  assert.equal((await POST(request({ host: "localhost:3007" }))).status, 403);
});

test("hosted deployment continues to require its studio password", async () => {
  process.env.STEM_STUDIO_LOCAL_WEB = "0";
  process.env.STEM_STUDIO_PASSWORD = "example-studio-password";
  assert.equal((await POST(request({ host: "studio.example", origin: "https://studio.example" }))).status, 401);
  assert.equal((await POST(request({ host: "studio.example", origin: "https://studio.example" }, JSON.stringify({ password: "example-studio-password" })))).status, 200);
});
