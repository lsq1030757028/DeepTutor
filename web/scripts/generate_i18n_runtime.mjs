import fs from "node:fs";
import path from "node:path";

const webRoot = path.resolve(process.cwd());
const sourcePath = path.join(webRoot, "locales", "en", "app.json");
const targetPath = path.join(webRoot, "i18n", "en-runtime.json");

const source = JSON.parse(fs.readFileSync(sourcePath, "utf8"));
const runtime = Object.fromEntries(
  Object.entries(source).filter(([key, value]) => value !== key),
);

fs.writeFileSync(targetPath, `${JSON.stringify(runtime, null, 2)}\n`, "utf8");
console.log(
  `[i18n:runtime] wrote ${Object.keys(runtime).length}/${Object.keys(source).length} entries`,
);
