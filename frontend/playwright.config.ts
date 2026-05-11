import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

const TEST_DIR = path.resolve(__dirname, "./tests/e2e");
const FAKE_AUDIO_PATH = path.resolve(__dirname, "./tests/fixtures/test_audio.wav");
const STORAGE_STATE_PATH = path.resolve(__dirname, "./tests/.auth/storage.json");

const reportDir =
    process.env.E2E_REPORT_DIR ||
    path.resolve(__dirname, "../test-reports/playwright");

export default defineConfig({
    testDir: TEST_DIR,
    timeout: 60_000,
    expect: { timeout: 10_000 },
    fullyParallel: false,
    workers: process.env.CI ? 1 : undefined,
    retries: process.env.CI ? 2 : 0,
    forbidOnly: !!process.env.CI,
    reporter: [
        ["list"],
        ["json", { outputFile: path.join(reportDir, "results.json") }],
        ["html", { open: "never", outputFolder: path.join(reportDir, "html") }],
        ["junit", { outputFile: path.join(reportDir, "junit.xml") }],
    ],
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
              reuseExistingServer: !process.env.CI,
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
            name: "chromium",
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
            testIgnore: [/visual\.spec\.ts/, /a11y\.spec\.ts/, /\.synthetic\.spec\.ts/],
        },
        {
            name: "visual",
            testMatch: /visual\.spec\.ts/,
            use: {
                ...devices["Desktop Chrome"],
                storageState: STORAGE_STATE_PATH,
                permissions: ["microphone"],
            },
        },
        {
            name: "a11y",
            testMatch: /a11y\.spec\.ts/,
            use: {
                ...devices["Desktop Chrome"],
                storageState: STORAGE_STATE_PATH,
            },
        },
    ],
});
