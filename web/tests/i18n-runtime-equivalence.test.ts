import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import path from "node:path";
import test from "node:test";

import { createInstance } from "i18next";

const WEB_ROOT = path.resolve(process.cwd());
const FULL_PATH = path.join(WEB_ROOT, "locales", "en", "app.json");
const RUNTIME_PATH = path.join(WEB_ROOT, "i18n", "en-runtime.json");

async function translator(resources: Record<string, unknown>) {
  const instance = createInstance();
  await instance.init({
    lng: "en",
    fallbackLng: false,
    resources: { en: { app: resources } },
    defaultNS: "app",
    ns: ["app"],
    keySeparator: false,
    interpolation: { escapeValue: false },
    returnEmptyString: false,
    returnNull: false,
  });
  return instance;
}

test("projected English runtime preserves every visible translation", async () => {
  const full = JSON.parse(readFileSync(FULL_PATH, "utf8")) as Record<string, unknown>;
  const runtime = JSON.parse(readFileSync(RUNTIME_PATH, "utf8")) as Record<
    string,
    unknown
  >;
  assert.deepEqual(
    runtime,
    Object.fromEntries(Object.entries(full).filter(([key, value]) => value !== key)),
  );

  const fullI18n = await translator(full);
  const runtimeI18n = await translator(runtime);
  const variants = [
    {},
    { count: 0 },
    { count: 1 },
    { count: 2 },
    { context: "male" },
    { context: "female" },
    { name: "Ada", port: 1457, seconds: 42 },
  ];

  for (const key of Object.keys(full)) {
    for (const options of variants) {
      assert.equal(runtimeI18n.t(key, options), fullI18n.t(key, options), key);
    }
  }
});

test("production fonts are local, licensed, and pinned", () => {
  const layout = readFileSync(path.join(WEB_ROOT, "app", "layout.tsx"), "utf8");
  assert.match(layout, /from "next\/font\/local"/);
  assert.doesNotMatch(layout, /next\/font\/google/);

  const fonts = new Map([
    [
      "geist-latin-variable.woff2",
      "9b6f5ff45b278c744b5f379a2c4ecbaf858a842b8eaf82ac8d21b699ca16c608",
    ],
    [
      "lora-latin-variable.woff2",
      "6b102ab35aa1f2b315788bb4853434ed1e52137603bf7a3da71a682276748d45",
    ],
  ]);
  for (const [filename, expectedHash] of fonts) {
    const bytes = readFileSync(path.join(WEB_ROOT, "app", "fonts", filename));
    assert.equal(createHash("sha256").update(bytes).digest("hex"), expectedHash);
  }

  const license = readFileSync(
    path.join(WEB_ROOT, "app", "fonts", "OFL.txt"),
    "utf8",
  );
  assert.match(license, /SIL OPEN FONT LICENSE Version 1\.1/);
  assert.match(license, /Copyright \(c\) 2023 Vercel/);
  assert.match(license, /Copyright 2011 The Lora Project Authors/);
});
