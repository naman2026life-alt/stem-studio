import assert from "node:assert/strict";
import test from "node:test";
import { isHealthyProcessor, isLoopbackProcessor, isMissingProcessorResource, processingErrorMessage, processorConnectionStatus, processorUnavailableMessage } from "../src/lib/processor-connection.ts";

test("readiness requires the processor's health response, not a tunnel page", () => {
  assert.equal(isHealthyProcessor({ status: "ok", model: "htdemucs" }), true);
  for (const body of [null, "OK", "<html>tunnel error</html>", {}, { status: "unavailable" }]) assert.equal(isHealthyProcessor(body), false);
});

test("a live Mac is not labelled ready when readiness checks fail", () => {
  assert.equal(processorConnectionStatus({ status: "ok" }, "mac"), "unavailable");
  assert.equal(processorConnectionStatus({ status: "not_ready", checks: { ffmpeg: false } }, "mac"), "attention");
  assert.equal(processorConnectionStatus({ status: "ready", checks: { ffmpeg: false } }, "mac"), "attention");
  assert.equal(processorConnectionStatus({ status: "ready", checks: { ffmpeg: true, remote_auth: true } }, "mac"), "ready");
  assert.equal(processorConnectionStatus({ status: "ok" }, "hosted"), "ready");
});

test("only literal loopback processor hosts are treated as available without internet", () => {
  for (const url of ["http://127.0.0.1:8766", "http://localhost:8766", "http://[::1]:8766"]) assert.equal(isLoopbackProcessor(url), true);
  for (const url of ["https://127.0.0.1.attacker.example", "https://processor.example", "invalid", "file://localhost/test"]) assert.equal(isLoopbackProcessor(url), false);
});

test("network failures explain Mac availability without losing useful validation errors", () => {
  assert.equal(processingErrorMessage(new TypeError("Failed to fetch"), "Fallback", "mac"), processorUnavailableMessage("mac"));
  assert.match(processingErrorMessage(new Error("Load failed"), "Fallback", "mac"), /Wake it up/);
  assert.match(processingErrorMessage(new Error("Processing failed (503)."), "Fallback", "mac"), /Wake it up/);
  assert.equal(processingErrorMessage(new Error("Clip start is beyond the recording."), "Fallback", "mac"), "Clip start is beyond the recording.");
  assert.doesNotMatch(processorUnavailableMessage("hosted"), /Mac/);
});

test("a sleeping Mac or disconnected tunnel never causes saved jobs to be forgotten", async () => {
  const tunnel404 = new Response("<html>Not Found</html>", { status: 404 });
  const generic404 = Response.json({ detail: "Not Found" }, { status: 404 });
  const unavailable = Response.json({ detail: "Unavailable" }, { status: 503 });
  for (const response of [tunnel404, generic404, unavailable]) assert.equal(await isMissingProcessorResource(response), false);
  assert.equal(await isMissingProcessorResource(Response.json({ detail: "This practice session expired. Analyze the recording again." }, { status: 410 })), true);
  assert.equal(await isMissingProcessorResource(Response.json({ detail: "This job expired or could not be found." }, { status: 404 })), true);
});
