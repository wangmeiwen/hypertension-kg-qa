import { spawnSync } from "node:child_process";

const allowedAdvisories = new Map([
  [
    "GHSA-mh99-v99m-4gvg",
    "仅由 ESLint 与 Workbox 构建期固定 glob 触发，项目不接收外部 glob；上游尚未提供兼容升级。",
  ],
  [
    "GHSA-qwww-vcr4-c8h2",
    "仅影响 React Router 的 RSC Server Action；本项目是纯客户端 SPA，不启用 RSC 或 Server Action。",
  ],
]);

const npmCli = process.env.npm_execpath;
if (!npmCli) {
  console.error("缺少 npm_execpath，请通过 npm run security:audit 执行安全门禁。");
  process.exit(1);
}

const result = spawnSync(
  process.execPath,
  [npmCli, "audit", "--json", "--registry=https://registry.npmjs.org"],
  { encoding: "utf8" },
);

if (result.error) {
  console.error(`无法执行 npm audit: ${result.error.message}`);
  process.exit(1);
}

let report;
try {
  report = JSON.parse(result.stdout);
} catch {
  console.error("npm audit 未返回有效 JSON。");
  console.error(result.stderr.trim());
  process.exit(1);
}

if (report.error) {
  console.error(`npm audit 请求失败: ${report.error.summary || report.message}`);
  process.exit(1);
}

const vulnerabilities = report.vulnerabilities ?? {};
const advisoryPattern = /GHSA-[0-9a-z-]+/i;

const resolveAdvisories = (name, seen = new Set()) => {
  if (seen.has(name)) return new Set();
  seen.add(name);

  const advisoryIds = new Set();
  for (const cause of vulnerabilities[name]?.via ?? []) {
    if (typeof cause === "string") {
      for (const advisory of resolveAdvisories(cause, seen)) advisoryIds.add(advisory);
      continue;
    }
    const advisory = cause.url?.match(advisoryPattern)?.[0];
    if (advisory) advisoryIds.add(advisory);
  }
  return advisoryIds;
};

const blocked = [];
const accepted = new Set();
for (const [name, vulnerability] of Object.entries(vulnerabilities)) {
  if (!["high", "critical"].includes(vulnerability.severity)) continue;

  const advisories = resolveAdvisories(name);
  if (
    advisories.size === 0 ||
    [...advisories].some((advisory) => !allowedAdvisories.has(advisory))
  ) {
    blocked.push(`${name} (${vulnerability.severity})`);
    continue;
  }
  for (const advisory of advisories) accepted.add(advisory);
}

if (blocked.length > 0) {
  console.error(`发现未豁免的高危或严重依赖漏洞: ${blocked.join(", ")}`);
  process.exit(1);
}

for (const advisory of accepted) {
  console.warn(`已接受范围受控的上游通告 ${advisory}: ${allowedAdvisories.get(advisory)}`);
}
console.log("前端依赖安全门禁通过：没有未豁免的高危或严重漏洞。");
