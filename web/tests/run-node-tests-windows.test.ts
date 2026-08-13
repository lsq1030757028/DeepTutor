import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const runnerPath = path.resolve(process.cwd(), "scripts/run-node-tests.mjs");

test("node test runner launches TypeScript through the current Node runtime", () => {
  const source = readFileSync(runnerPath, "utf8");

  assert.match(source, /run\(process\.execPath/);
  assert.match(source, /"typescript", "bin", "tsc"/);
});

test("node test runner fails closed when require(esm) is unavailable", () => {
  const source = readFileSync(runnerPath, "utf8");

  assert.match(source, /process\.features\.require_module !== true/);
  assert.match(source, /Node >=22\.12/);
  assert.ok(
    source.indexOf("process.features.require_module") < source.indexOf("rmSync(distRoot"),
    "runtime compatibility must be checked before the test build is mutated",
  );
});
