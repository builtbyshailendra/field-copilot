import { defineConfig, devices } from "@playwright/test";

// Starts the copilot API (fake model, so no key) and Next.js, then drives the app in a browser.
// PYTHON picks the interpreter; PW_CHANNEL picks an installed browser (msedge on Windows).
const CI = Boolean(process.env.CI);
export const DEMO_PASSWORD = process.env.FIELD_DEMO_PASSWORD ?? "e2e-demo-password";
const SERVER = process.env.FIELD_E2E_SERVER ?? "postgresql://postgres@127.0.0.1:5433";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 20_000 },
  retries: CI ? 1 : 0,
  workers: 1,
  reporter: CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: { baseURL: "http://127.0.0.1:3100", trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], channel: process.env.PW_CHANNEL || undefined } }],
  webServer: [
    {
      command: `${process.env.PYTHON ?? "python"} -m uvicorn fieldcopilot.api.app:app --host 127.0.0.1 --port 8020`,
      url: "http://127.0.0.1:8020/healthz",
      reuseExistingServer: !CI,
      timeout: 120_000,
      env: {
        FIELD_ADMIN_DSN: `${SERVER}/fieldcopilot_e2e`,
        FIELD_APP_DSN: `postgresql://copilot_app:copilot-app-dev@${SERVER.split("@")[1]}/fieldcopilot_e2e`,
        FIELD_DEMO_PASSWORD: DEMO_PASSWORD,
        FIELD_LLM_MODE: "fake",
        FIELD_EMBEDDER: "hashing",
        APP_ENV: "dev",
      },
    },
    {
      command: CI ? "npm run build && npm run start" : "npm run dev",
      url: "http://127.0.0.1:3100/login",
      reuseExistingServer: !CI,
      timeout: 180_000,
    },
  ],
});
