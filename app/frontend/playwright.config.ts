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
