import { spawn } from "node:child_process";
import { once } from "node:events";
import { resolve } from "node:path";

const frontendRoot = process.cwd();
const repositoryRoot = resolve(frontendRoot, "../..");
const backendUrl = "http://127.0.0.1:8001/health/readiness";
const frontendUrl = "http://127.0.0.1:4173/";
const startupTimeoutMs = 120_000;
const pollIntervalMs = 250;

const sleep = (milliseconds) =>
  new Promise((resolveSleep) => {
    setTimeout(resolveSleep, milliseconds);
  });

const isReachable = async (url) => {
  try {
    const response = await fetch(url, { signal: AbortSignal.timeout(1_000) });
    return response.status < 500;
  } catch {
    return false;
  }
};

const assertPortAvailable = async (url) => {
  if (await isReachable(url)) {
    throw new Error(`E2E 服务地址已被占用: ${url}`);
  }
};

const waitForServer = async (name, url, child) => {
  const deadline = Date.now() + startupTimeoutMs;
  while (Date.now() < deadline) {
    if (child.exitCode !== null) {
      throw new Error(`${name} 在就绪前退出，exit_code=${child.exitCode}`);
    }
    if (await isReachable(url)) return;
    await sleep(pollIntervalMs);
  }
  throw new Error(`${name} 启动超时: ${url}`);
};

const terminateProcess = async (child) => {
  if (child.exitCode !== null || child.pid === undefined) return;

  if (process.platform === "win32") {
    const exited = once(child, "exit");
    child.kill("SIGKILL");
    await exited;
    return;
  }

  child.kill("SIGTERM");
  await Promise.race([once(child, "exit"), sleep(3_000)]);
  if (child.exitCode === null) {
    const exited = once(child, "exit");
    child.kill("SIGKILL");
    await exited;
  }
};

const runChild = (command, args, options) =>
  spawn(command, args, {
    ...options,
    stdio: "inherit",
    windowsHide: true,
  });

await Promise.all([assertPortAvailable(backendUrl), assertPortAvailable(frontendUrl)]);

const backend = runChild(
  "python",
  [
    "-m",
    "uvicorn",
    "app.backend.testing.deterministic_app:create_app",
    "--factory",
    "--host",
    "127.0.0.1",
    "--port",
    "8001",
  ],
  {
    cwd: repositoryRoot,
    env: {
      ...process.env,
      ADMIN_USERNAME: "admin",
      ADMIN_PASSWORD: "admin123", // pragma: allowlist secret
      ALLOWED_ORIGINS: "http://127.0.0.1:4173,http://localhost:4173",
    },
  },
);

const frontend = runChild(
  process.execPath,
  [resolve(frontendRoot, "node_modules/vite/bin/vite.js"), "--host", "127.0.0.1", "--port", "4173"],
  {
    cwd: frontendRoot,
    env: {
      ...process.env,
      VITE_BACKEND_URL: "http://127.0.0.1:8001",
    },
  },
);
const activeChildren = [frontend, backend];

let cleanupStarted = false;
const cleanup = async () => {
  if (cleanupStarted) return;
  cleanupStarted = true;
  await Promise.all(activeChildren.map(terminateProcess));
};

const handleSignal = () => {
  void cleanup().then(() => {
    process.exitCode = 1;
  });
};

process.once("SIGINT", handleSignal);
process.once("SIGTERM", handleSignal);

try {
  await Promise.all([
    waitForServer("确定性后端", backendUrl, backend),
    waitForServer("前端开发服务器", frontendUrl, frontend),
  ]);

  const playwright = runChild(
    process.execPath,
    [
      resolve(frontendRoot, "node_modules/@playwright/test/cli.js"),
      "test",
      ...process.argv.slice(2),
    ],
    { cwd: frontendRoot, env: process.env },
  );
  activeChildren.push(playwright);
  const [exitCode] = await once(playwright, "exit");
  process.exitCode = typeof exitCode === "number" ? exitCode : 1;
} finally {
  await cleanup();
}
