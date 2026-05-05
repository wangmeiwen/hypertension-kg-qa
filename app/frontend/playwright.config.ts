import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: {
    timeout: 10_000,
  },
  use: {
    baseURL: "http://127.0.0.1:4173",
    trace: "on-first-retry",
  },
  webServer: [
    {
      // E2E 使用确定性后端，避免真实 Neo4j/Milvus/LLM 依赖影响前端回归。
      command:
        "python -m uvicorn app.backend.testing.deterministic_app:create_app --factory --host 127.0.0.1 --port 8001",
      cwd: "../..",
      port: 8001,
      reuseExistingServer: false,
      env: {
        ...process.env,
        ADMIN_USERNAME: "admin",
        ADMIN_PASSWORD: "admin123",
        ALLOWED_ORIGINS: "http://127.0.0.1:4173,http://localhost:4173",
      },
    },
    {
      // 前端使用固定预览端口，并把后端地址指向确定性服务。
      command: "npm run dev -- --host 127.0.0.1 --port 4173",
      port: 4173,
      reuseExistingServer: !process.env.CI,
      env: {
        ...process.env,
        VITE_BACKEND_URL: "http://127.0.0.1:8001",
      },
    },
  ],
});
