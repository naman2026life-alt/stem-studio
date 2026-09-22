import assert from "node:assert/strict";
import test from "node:test";
import { contourPath, hzToMidi, noteLabel, parsePracticeTime, pitchDescription, safePracticeId } from "../src/lib/practice.ts";

test("pitch stays continuous in cents and uses logarithmic note spacing", () => {
  assert.equal(hzToMidi(440), 69);
  assert.equal(hzToMidi(880), 81);
  assert.ok(Math.abs(hzToMidi(440 * 2 ** (35 / 1200)) - 69.35) < 1e-8);
  assert.equal(hzToMidi(null), null);
  assert.equal(hzToMidi(0), null);
  assert.equal(hzToMidi(Infinity), null);
  assert.equal(pitchDescription(69.35), "35¢ sharp");
  assert.equal(pitchDescription(68.65), "35¢ flat");
});

test("Sa is selected explicitly and chromatic swaras distinguish komal and tivra", () => {
  assert.equal(noteLabel(60), "C4");
  assert.equal(noteLabel(62, 2), "Sa · D4");
  assert.equal(noteLabel(63, 2), "re · D♯4");
  assert.equal(noteLabel(65, 2), "ga · F4");
  assert.equal(noteLabel(68, 2), "Ma♯ · G♯4");
  assert.equal(noteLabel(71, 2), "Dha · B4");
});

test("contour never draws through unvoiced audio or missing time", () => {
  const points = [{ time: 0, midi: 60 }, { time: .01, midi: 60.1 }, { time: .02, midi: null }, { time: .03, midi: 60.2 }, { time: 1, midi: 64 }];
  const path = contourPath(points, 0, 2, (time) => time * 100, (midi) => midi, .03);
  assert.equal(path, "M0.00,60.00 L1.00,60.10 M3.00,60.20 M100.00,64.00 ");
  assert.equal(contourPath(points, 2, 3, (value) => value, (value) => value, .03), "");
});

test("clip input supports seconds and source timestamps and rejects ambiguous text", () => {
  assert.equal(parsePracticeTime("75.5"), 75.5);
  assert.equal(parsePracticeTime("1:15.5"), 75.5);
  assert.equal(parsePracticeTime("1:01:15"), 3675);
  for (const value of ["", "-2", "1::2", "ten", "1:2:3:4"]) assert.ok(Number.isNaN(parsePracticeTime(value)));
});

test("restored job IDs cannot introduce URL paths or queries", () => {
  assert.equal(safePracticeId("abcde1234-fg56"), true);
  for (const value of [null, {}, "", "../../jobs", "abcde1234?x=1", "https://evil.example"]) assert.equal(safePracticeId(value), false);
});
