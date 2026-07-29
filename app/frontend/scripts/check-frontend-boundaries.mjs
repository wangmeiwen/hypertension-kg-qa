/* global console, process */

import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const root = process.cwd();
const srcDir = join(root, "src");
const allowedRootFiles = new Set(["App.tsx", "main.tsx"]);
const violations = [];

function walk(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return walk(path);
    return path;
  });
}

for (const entry of readdirSync(srcDir)) {
  const fullPath = join(srcDir, entry);
  if (statSync(fullPath).isFile() && !allowedRootFiles.has(entry)) {
    violations.push(`src 根目录只允许入口 shim，发现：${entry}`);
  }
}

for (const file of walk(srcDir)) {
  const rel = relative(root, file).replaceAll("\\", "/");
  if (/\.(js|jsx)$/.test(file)) {
    violations.push(`业务源码不允许回流 .js/.jsx：${rel}`);
  }

  if (/src\/css\/\d+_.*\.css$/.test(rel)) {
    violations.push(`旧编号 CSS 必须退役：${rel}`);
  }

  const text = readFileSync(file, "utf8");
  if (
    /from\s+["'][^"']*\/css\/\d+_.*\.css["']/.test(text) ||
    /@import\s+["'][^"']*\d+_.*\.css["']/.test(text)
  ) {
    violations.push(`禁止引用旧编号 CSS：${rel}`);
  }

  if (
    /from\s+["'][^"']*src\/styles\.css["']/.test(text) ||
    /["'][^"']*\/styles\.css["']/.test(text)
  ) {
    violations.push(`禁止引用旧 src/styles.css：${rel}`);
  }
}

if (violations.length > 0) {
  console.error(violations.join("\n"));
  process.exit(1);
}

console.log("Frontend boundary check passed.");
