import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

const SCRIPT = path.resolve(process.cwd(), "scripts", "route_budgets.mjs");

test("route budget parser supports full project keys and fails closed", () => {
  const source = readFileSync(SCRIPT, "utf8");
  assert.match(source, /key\.endsWith\("\/app\/layout"\)/);
  assert.match(source, /key\.endsWith\(`\/app\$\{manifestKey\}`\)/);
  assert.match(source, /routeRows\.length === 0/);
  assert.match(source, /rootShellSize === 0/);
  assert.match(source, /process\.exit\(2\)/);
});
