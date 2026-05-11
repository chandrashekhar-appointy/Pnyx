/**
 * Synthetic monitoring config — runs the happy path against PROD every 15 min
 * (or whatever cadence the cron workflow chooses).  Distinct from the dev
 * Playwright config because:
 *
 *  * No webServer (target is the deployed frontend)
 *  * No fake media flags (real prod, no recording test runs here)
 *  * Read-only assertions: the UI loads, login renders, no 5xx, /api/auth/csrf
 *    responds.  We do *not* attempt to record on prod.
 *
 * Trigger: .github/workflows/synthetic-monitoring.yml.
 */
import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

const REPORT_DIR =
    process.env.E2E_REPORT_DIR ||
    path.resolve(__dirname, "../test-reports/synthetic");

export default defineConfig({
    testDir: path.resolve(__dirname, "./tests/synthetic"),
    timeout: 30_000,
    expect: { timeout: 10_000 },
    fullyParallel: true,
    workers: 2,
    retries: 1,
    reporter: [
        ["list"],
        ["json", { outputFile: path.join(REPORT_DIR, "results.json") }],
        ["junit", { outputFile: path.join(REPORT_DIR, "junit.xml") }],
    ],
    use: {
        baseURL: process.env.SYNTHETIC_BASE_URL || "https://pnyx-dev-206432.bifrost.saastack.site",
        trace: "retain-on-failure",
        screenshot: "only-on-failure",
        video: "off",
        ignoreHTTPSErrors: false,
    },
    projects: [
        {
            name: "chromium",
            use: { ...devices["Desktop Chrome"] },
        },
    ],
});
