// [fork] 旅程薄壳 + 富卡的 i18n 机械守。
//
// ## 为什么不能靠 eslint 的 warning 数
//
// `eslint/i18n-plugin.mjs` 的 `no-literal-ui-text` 只挂 `JSXText` 与 `JSXAttribute`。
// 文案一旦集中进普通对象（`copy.ts`、`VERDICT_STYLE`、`SEGMENT_MAP`），
// 那条规则**完全看不见**——"warning 数没涨"证明不了没有硬编码。
// 所以判据换维度：直接枚举源码里的中文字面量，以及逐个核对引用到的键在不在两个语言里。
//
// 两条断言各守一半：
//   ① 键完整性：代码里 `t("X")` 引用的每个 X，en 与 zh 都得有真值；
//   ② 零中文字面量：注释之外不许再出现中文串（注释里的设计说明不算 UI 文案）。

import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";

const WEB = process.cwd();
const SURFACE = path.join(WEB, "components", "test-journey");

function listSources(dir: string): string[] {
  const out: string[] = [];
  for (const ent of readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, ent.name);
    if (ent.isDirectory()) out.push(...listSources(full));
    else if (ent.name.endsWith(".ts") || ent.name.endsWith(".tsx")) out.push(full);
  }
  return out;
}

/** 注释是设计说明，不是 UI 文案——先剥掉，免得把注释里的中文当成漏网的硬编码。 */
function stripComments(src: string): string {
  let out = "";
  let i = 0;
  while (i < src.length) {
    const c = src[i];
    if (c === "/" && src[i + 1] === "/") {
      while (i < src.length && src[i] !== "\n") i += 1;
      continue;
    }
    if (c === "/" && src[i + 1] === "*") {
      i += 2;
      while (i + 1 < src.length && !(src[i] === "*" && src[i + 1] === "/")) i += 1;
      i += 2;
      continue;
    }
    if (c === '"' || c === "'" || c === "`") {
      const quote = c;
      out += c;
      i += 1;
      while (i < src.length) {
        if (src[i] === "\\") {
          out += src.slice(i, i + 2);
          i += 2;
          continue;
        }
        out += src[i];
        if (src[i] === quote) {
          i += 1;
          break;
        }
        i += 1;
      }
      continue;
    }
    out += c;
    i += 1;
  }
  return out;
}

const SOURCES = listSources(SURFACE).map((p) => ({
  rel: path.relative(WEB, p).replaceAll("\\", "/"),
  src: readFileSync(p, "utf8"),
}));

test("旅程薄壳有源码可扫（阳性对照：文件集不是空的）", () => {
  // 「扫描无命中」在文件列表为空时也成立。先把这条排除掉。
  assert.ok(SOURCES.length >= 12, `只扫到 ${SOURCES.length} 个源文件，路径多半错了`);
  assert.ok(
    SOURCES.some((f) => f.rel.endsWith("chat/copy.ts")),
    "没扫到 chat/copy.ts —— 文案的主集中地",
  );
});

test("代码引用的每个 i18n 键，en 与 zh 都有真值", () => {
  const en = JSON.parse(
    readFileSync(path.join(WEB, "locales", "en", "app.json"), "utf8"),
  ) as Record<string, unknown>;
  const zh = JSON.parse(
    readFileSync(path.join(WEB, "locales", "zh", "app.json"), "utf8"),
  ) as Record<string, unknown>;

  // 两种引用形态：直接 t("X")；以及查表结构里存的 "journey.*" 键（渲染处才 t()）。
  const call = /\b(?:i18n\.)?t\(\s*\n?\s*"((?:[^"\\]|\\.)*)"/g;
  const dotted = /"(journey\.[A-Za-z0-9.]+)"/g;

  const refs = new Map<string, string>(); // key -> 第一次出现的文件
  for (const { rel, src } of SOURCES) {
    for (const m of src.matchAll(call)) {
      const key = JSON.parse(`"${m[1]}"`) as string;
      if (!refs.has(key)) refs.set(key, rel);
    }
    for (const m of src.matchAll(dotted)) {
      if (!refs.has(m[1])) refs.set(m[1], rel);
    }
  }

  assert.ok(refs.size >= 100, `只认出 ${refs.size} 个键，取数正则多半失灵了`);

  const missing: string[] = [];
  for (const [key, where] of refs) {
    for (const [lang, table] of [
      ["en", en],
      ["zh", zh],
    ] as const) {
      const v = table[key];
      if (typeof v !== "string" || v.trim() === "") {
        missing.push(`${lang}: ${key}  (${where})`);
      }
    }
  }
  assert.deepEqual(missing, [], `缺键 / 空值：\n${missing.join("\n")}`);
});

test("旅程薄壳与富卡的源码里没有中文 UI 字面量（注释除外）", () => {
  const cjk = /[㐀-鿿　-〿＀-￯]+/g;
  const found: string[] = [];
  for (const { rel, src } of SOURCES) {
    const code = stripComments(src);
    for (const m of code.matchAll(cjk)) {
      found.push(`${rel}: ${m[0]}`);
    }
  }
  assert.deepEqual(
    found,
    [],
    `还有中文没外提（共 ${found.length} 处）：\n${found.join("\n")}`,
  );
});
