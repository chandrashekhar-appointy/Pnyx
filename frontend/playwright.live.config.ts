/**
 * Playwright config for the un-stubbed live E2E suite.
 *
 * Run with:   E2E_LIVE=1 pnpm run test:e2e:live
 *
 * Differences from the default config:
 *  - testDir: tests/e2e/live   (only the live specs)
 *  - 2-minute per-test timeout (notes generation can take ~60s)
 *  - reuseExistingServer: true — if you have the dev server already running
 *    with PLAYWRIGHT_TESTING=true, it will be reused
 *  - workers: 1 (tests record real meetings; parallelism would conflict)
 */
import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

const FAKE_AUDIO_PATH = path.resolve(__dirname, "./tests/fixtures/test_audio.wav");
const STORAGE_STATE_PATH = path.resolve(__dirname, "./tests/.auth/storage.json");

export default defineConfig({
    testDir: path.resolve(__dirname, "./tests/e2e/live"),
    timeout: 120_000,
    expect: { timeout: 15_000 },
    fullyParallel: false,
    workers: 1,
    retries: 0,
    reporter: [["list"]],
    globalSetup: path.resolve(__dirname, "./tests/global-setup.ts"),
    use: {
        baseURL: process.env.E2E_BASE_URL || "http://localhost:3118",
        trace: "retain-on-failure",
        screenshot: "only-on-failure",
        video: "retain-on-failure",
    },
    webServer: process.env.E2E_NO_WEBSERVER
        ? undefined
        : {
              command: "pnpm run dev",
              url: "http://localhost:3118",
              reuseExistingServer: true,
              timeout: 120_000,
              env: {
                  NEXTAUTH_SECRET:
                      process.env.NEXTAUTH_SECRET || "test-secret-do-not-use-in-prod",
                  NEXTAUTH_URL: "http://localhost:3118",
                  NEXT_PUBLIC_BACKEND_URL:
                      process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:5167",
                  PLAYWRIGHT_TESTING: "true",
              },
          },
    projects: [
        {
            name: "chromium-live",
            use: {
                ...devices["Desktop Chrome"],
                storageState: STORAGE_STATE_PATH,
                permissions: ["microphone"],
                launchOptions: {
                    args: [
                        "--use-fake-ui-for-media-stream",
                        "--use-fake-device-for-media-stream",
                        `--use-file-for-fake-audio-capture=${FAKE_AUDIO_PATH}`,
                        "--autoplay-policy=no-user-gesture-required",
                    ],
                },
            },
        },
    ],
});
