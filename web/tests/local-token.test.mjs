import assert from "node:assert/strict";
import { afterEach, beforeEach, test } from "node:test";
import { POST } from "../src/app/api/upload-token/route.ts";

let original;
beforeEach(() => {
  original = { local: process.env.STEM_STUDIO_LOCAL_WEB, secret: process.env.PROCESSOR_SHARED_SECRET, password: process.env.STEM_STUDIO_PASSWORD, node: process.env.NODE_ENV, vercel: process.env.VERCEL };
  process.env.STEM_STUDIO_LOCAL_WEB = "1";
  process.env.PROCESSOR_SHARED_SECRET = "test-only-secret-that-is-never-a-real-credential";
  process.env.STEM_STUDIO_PASSWORD = "";
  process.env.NODE_ENV = "test";
  delete process.env.VERCEL;
});
afterEach(() => {
  for (const [key, value] of [["STEM_STUDIO_LOCAL_WEB", original.local], ["PROCESSOR_SHARED_SECRET", original.secret], ["STEM_STUDIO_PASSWORD", original.password], ["NODE_ENV", original.node], ["VERCEL", original.vercel]]) {
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
  process.env.NODE_ENV = "production";
  process.env.STEM_STUDIO_PASSWORD = "example-studio-password";
  assert.equal((await POST(request({ host: "studio.example", origin: "https://studio.example" }))).status, 401);
  assert.equal((await POST(request({ host: "studio.example", origin: "https://studio.example" }, JSON.stringify({ password: "example-studio-password" })))).status, 200);
});

test("hosted production never issues signatures when its password is missing", async () => {
  process.env.STEM_STUDIO_LOCAL_WEB = "0";
  process.env.NODE_ENV = "production";
  for (const password of [undefined, "", "   "]) {
    if (password === undefined) delete process.env.STEM_STUDIO_PASSWORD;
    else process.env.STEM_STUDIO_PASSWORD = password;
    const response = await POST(request());
    assert.equal(response.status, 503);
    assert.equal((await response.json()).signature, undefined);
  }
});

test("production refuses missing or too-short processor signing secrets", async () => {
  process.env.STEM_STUDIO_LOCAL_WEB = "0";
  process.env.NODE_ENV = "production";
  process.env.STEM_STUDIO_PASSWORD = "test-password";
  for (const secret of ["", "short"]) {
    process.env.PROCESSOR_SHARED_SECRET = secret;
    assert.equal((await POST(request({}, JSON.stringify({ password: "test-password" })))).status, 503);
  }
});

test("the production loopback UI retains its carefully scoped password exception", async () => {
  process.env.NODE_ENV = "production";
  const response = await POST(request());
  assert.equal(response.status, 200);
  assert.match((await response.json()).signature, /^[a-f0-9]{64}$/);
});

test("Vercel cannot accidentally enable the passwordless local-only exception", async () => {
  process.env.VERCEL = "1";
  assert.equal((await POST(request())).status, 503);
});

test("malformed and oversized password bodies return bounded client errors", async () => {
  process.env.STEM_STUDIO_LOCAL_WEB = "0";
  process.env.VERCEL = "1";
  process.env.STEM_STUDIO_PASSWORD = "test-password";
  const headers = { "x-forwarded-for": "192.0.2.11" };
  for (const password of [123, {}, [], null]) assert.equal((await POST(request(headers, JSON.stringify({ password })))).status, 401);
  assert.equal((await POST(request(headers, "{"))).status, 400);
  assert.equal((await POST(request(headers, JSON.stringify({ password: "x".repeat(5000) })))).status, 413);
  assert.equal((await POST(request({ ...headers, "content-type": "text/plain" }, "{}"))).status, 415);
});

test("repeated wrong passwords are temporarily throttled within a warm process", async () => {
  process.env.STEM_STUDIO_LOCAL_WEB = "0";
  process.env.VERCEL = "1";
  process.env.STEM_STUDIO_PASSWORD = "test-password";
  const headers = { "x-forwarded-for": "192.0.2.12" };
  for (let index = 0; index < 8; index++) assert.equal((await POST(request(headers, JSON.stringify({ password: "wrong" })))).status, 401);
  const response = await POST(request(headers, JSON.stringify({ password: "wrong" })));
  assert.equal(response.status, 429);
  assert.ok(Number(response.headers.get("retry-after")) > 0);
  assert.equal((await response.json()).signature, undefined);
  assert.equal((await POST(request({ "x-forwarded-for": "192.0.2.13" }, JSON.stringify({ password: "test-password" })))).status, 200);
});

test("successful authorization resets earlier failures for that client", async () => {
  process.env.STEM_STUDIO_LOCAL_WEB = "0";
  process.env.VERCEL = "1";
  process.env.STEM_STUDIO_PASSWORD = "test-password";
  const headers = { "x-forwarded-for": "192.0.2.14" };
  for (let index = 0; index < 7; index++) assert.equal((await POST(request(headers, "{}"))).status, 401);
  assert.equal((await POST(request(headers, JSON.stringify({ password: "test-password" })))).status, 200);
  assert.equal((await POST(request(headers, "{}"))).status, 401);
});
