/**
 * Un-stubbed end-to-end happy-path test.
 *
 * Unlike the standard stubbed suite, this test makes REAL calls to a running
 * backend (localhost:5167 by default). It proves the full stack works:
 *   Browser fake-mic → WebSocket → VAD → Groq/ElevenLabs → transcript → DB
 *   → stop recording → notes generated → notes appear in meeting-details
 *
 * RUNNING LOCALLY
 * ---------------
 *   1. Start the backend: cd backend && ./run-docker.sh  (or ./clean_start_backend.sh)
 *   2. Run this suite:
 *        E2E_LIVE=1 pnpm run test:e2e:live
 *
 * The frontend dev server is auto-started (with PLAYWRIGHT_TESTING=true) by
 * the playwright.live.config.ts config, OR you can pre-start it:
 *        PLAYWRIGHT_TESTING=true pnpm run dev
 *        E2E_LIVE=1 E2E_NO_WEBSERVER=1 pnpm run test:e2e:live
 *
 * The spec is SKIPPED automatically if E2E_LIVE is not set, so running
 *   `pnpm run test:e2e`  (the default CI suite) is not affected.
 */

import { expect, test } from "@playwright/test";
import { stubAuthSession } from "../_helpers";

const LIVE = !!process.env.E2E_LIVE;
const BACKEND = process.env.NEXT_PUBLIC_BACKEND_URL ?? "http://localhost:5167";

test.describe("Live happy-path: record → transcript → notes", () => {
    test.skip(!LIVE, "Set E2E_LIVE=1 to run this suite against a real backend");

    test.use({
        // Fake-mic via the WAV file set in playwright.config.ts.
        permissions: ["microphone"],
    });

    test.beforeEach(async ({ page }) => {
        // Auth stub so we don't need a real Google OAuth token —
        // the backend trust is covered by PLAYWRIGHT_TESTING on the server.
        await stubAuthSession(page, {
            email: "e2e-live@appointy.com",
            name: "E2E Live User",
        });
    });

    test("start recording, receive at least one transcript, stop, notes generate", async ({
        page,
    }) => {
        // ── 1. Navigate to the home page ────────────────────────────────────
        const res = await page.goto("/", { waitUntil: "domcontentloaded" });
        expect(res?.status()).toBeLessThan(500);

        // ── 2. Verify the backend is reachable from this machine ────────────
        const health = await fetch(`${BACKEND}/health`).catch(() => null);
        if (!health?.ok) {
            test.skip(true, `Backend at ${BACKEND} not reachable — start it first`);
            return;
        }

        // ── 3. Click Start Pnyx ─────────────────────────────────────────────
        const startBtn = page
            .getByRole("button", { name: /start pnyx/i })
            .first();
        await expect(startBtn).toBeVisible({ timeout: 10_000 });
        await startBtn.click();

        // ── 4. Wait for a real transcript to arrive from the backend ────────
        // The fake-mic WAV (220 Hz sine, 3 s) keeps looping — VAD will flag it
        // as speech and Groq will return something. We just need ANY non-empty
        // transcript to appear in the UI (partial or final).
        await expect
            .poll(
                async () => {
                    // Look for any visible text that is NOT a UI label.
                    const transcriptArea = page.locator(
                        "[data-testid='transcript-panel'], .transcript-text, [class*='transcript']"
                    );
                    if ((await transcriptArea.count()) === 0) return false;
                    const text = await transcriptArea.first().innerText().catch(() => "");
                    return text.trim().length > 0;
                },
                {
                    timeout: 40_000,
                    intervals: [2_000],
                    message: "Expected a real transcript to appear within 40s",
                },
            )
            .toBeTruthy();

        // ── 5. Stop recording ────────────────────────────────────────────────
        const stopBtn = page
            .getByRole("button", { name: /stop|end|finish/i })
            .first();
        await expect(stopBtn).toBeVisible({ timeout: 5_000 });
        await stopBtn.click();

        // ── 6. Wait for notes/summary to be generated and page to redirect ───
        // After stop, the app should navigate to meeting-details and show notes.
        await page.waitForURL(/meeting-details/, { timeout: 60_000 });

        // ── 7. Assert notes exist in meeting-details ─────────────────────────
        await expect
            .poll(
                async () => {
                    const content = await page.content();
                    // Notes section should contain some substantive text once
                    // the LLM finishes. Look for heading-level markers.
                    return (
                        content.includes("Key Points") ||
                        content.includes("Action Items") ||
                        content.includes("Summary") ||
                        content.includes("Decisions")
                    );
                },
                {
                    timeout: 90_000, // notes generation can take up to ~60s
                    intervals: [3_000],
                    message: "Expected generated notes to appear in meeting-details",
                },
            )
            .toBeTruthy();
    });
});
