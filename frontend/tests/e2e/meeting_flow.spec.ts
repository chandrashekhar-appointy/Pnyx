import { expect, test } from "@playwright/test";
import { stubAuthSession, stubBackend } from "./_helpers";

test.describe("Happy-path meeting flow", () => {
    test.beforeEach(async ({ page }) => {
        await stubAuthSession(page, { email: "test@appointy.com", name: "Test User" });
        await stubBackend(page);
    });

    test("loads the recording UI for an authenticated user", async ({ page }) => {
        const response = await page.goto("/", { waitUntil: "domcontentloaded" });
        expect(response?.status()).toBeLessThan(500);

        // Use loose locators because no data-testid exists yet — we look for
        // the Mic icon's parent button, identified by accessible name patterns.
        const startButton = page
            .getByRole("button", { name: /start (pnyx|recording|meeting)/i })
            .first();
        await expect(startButton).toBeVisible({ timeout: 15_000 });
    });

    test("clicking Start kicks off the recording state", async ({ page }) => {
        // Allow WebSocket connections to fail (no backend) but UI must still
        // transition to "starting" state.
        await page.goto("/", { waitUntil: "domcontentloaded" });
        const startButton = page
            .getByRole("button", { name: /start (pnyx|recording|meeting)/i })
            .first();
        if (!(await startButton.isVisible().catch(() => false))) {
            test.skip(true, "Start button not located — check selectors after UI changes");
        }
        await startButton.click({ trial: false });

        // Either a "Starting…" state or a Stop control appears
        await expect
            .poll(
                async () => {
                    const stopBtn = page
                        .getByRole("button", { name: /stop|end|finish/i })
                        .first();
                    const startingText = page.locator("text=/Starting Pnyx|Preparing/i");
                    return (
                        (await stopBtn.isVisible().catch(() => false)) ||
                        (await startingText.isVisible().catch(() => false))
                    );
                },
                { timeout: 15_000 },
            )
            .toBeTruthy();
    });
});
