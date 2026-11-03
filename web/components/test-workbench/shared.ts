// [fork] 测试工作台各屏共用的小件。设计稿 extensions/test-partner/docs/design/
// workbench-full.html（决策 0010）。

import { apiFetch, apiUrl } from "@/lib/api";

export const BASE = "/api/v1/test-workbench";

export async function readError(res: Response): Promise<string> {
  // 后端的 detail 有时是字符串、有时是 {code, message}（执行/导出面统一这么报）。
  // 两种都要能读出人话——把 [object Object] 甩给用户等于没报错。
  try {
    const body = await res.json();
    const d = body?.detail;
    if (typeof d === "string") return d;
    if (d && typeof d === "object") return d.message || d.hint || JSON.stringify(d);
    return res.statusText;
  } catch {
    return res.statusText;
  }
}

export async function downloadBlob(path: string, filename: string): Promise<void> {
  // 走 apiFetch 拿 blob 再触发下载，而不是 <a href>：下载端点在鉴权后面，
  // 裸链接不带凭据头，拿到的会是 401 页面存成的假文件。
  const res = await apiFetch(apiUrl(path));
  if (!res.ok) throw new Error(await readError(res));
  const blob = await res.blob();
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = filename;
  link.click();
  URL.revokeObjectURL(link.href);
}
