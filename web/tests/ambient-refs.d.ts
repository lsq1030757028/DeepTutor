// [fork] 把仓库根的环境声明拉进 node 测试的编译程序。
//
// `tsconfig.node-tests.json` 的 `include` 只有 `tests/**/*.ts`，而环境声明
// （`types/*.d.ts`）只能经 `include` / `files` / 三斜线引用进入程序——**跟着
// import 走是进不来的**。主 `tsconfig.json` 的 include 覆盖了 `**/*.ts` 所以没事，
// 到了这里就缺。
//
// 触发点：`tests/capability-picker.test.ts` 要 import 真的 home page 模块，
// 它的模块图里有 `lib/chat-import/*`，那几个文件用的是 File System Access API。
// 缺声明时 `tsc -p tsconfig.node-tests.json` 报 7 个 TS2339，**整个 test:node
// 在编译阶段就退出**（一条测试都跑不了）。
//
// 用新增文件而不是改 `tsconfig.node-tests.json`：后者是上游既有文件，改它要占
// 一行登记（`extensions/UPSTREAM-TOUCHPOINTS.md`），而 M2 只剩 1 行硬余量。
// 新增文件与上游零冲突、免登记。

/// <reference path="../types/file-system-access.d.ts" />

export {};
