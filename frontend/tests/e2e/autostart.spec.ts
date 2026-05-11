import { expect, test } from "@playwright/test";
import { stubAuthSession, stubBackend } from "./_helpers";

test.describe("Calendar autostart flow", () => {
    test.beforeEach(async ({ page }) => {
        await stubAuthSession(page, { email: "test@appointy.com", name: "Test User" });
        await stubBackend(page);
    });

    test("preserves autoStart query params through the auth redirect", async ({
        page,
    }) => {
        await page.goto("/login?autoStart=true&meetingTitle=Sync&source=calendar_email", {
            waitUntil: "domcontentloaded",
        });
        // The login page should render or redirect — but the URL preservation
        // is the contract.  After landing on `/`, the params (or sessionStorage
        // sentinel) must still be visible.
        await page.waitForLoadState("networkidle").catch(() => undefined);
        const url = page.url();
        const sessionAutoStart = await page.evaluate(() =>
            window.sessionStorage.getItem("autoStartRecording"),
        );
        const preservedInUrl = url.includes("autoStart=true");
        expect(preservedInUrl || sessionAutoStart === "true").toBeTruthy();
    });

    test("auto-triggers recording when autoStart=true is present on /", async ({
        page,
    }) => {
        await page.goto("/?autoStart=true&meetingTitle=Sync", {
            waitUntil: "domcontentloaded",
        });
        // We don't strictly enforce that the WS connects, but the UI must
        // either show a Stop control OR a "Starting" state within a few
        // seconds — proving it didn't wait for a manual click.
        await expect
            .poll(
                async () => {
                    const stop = page.getByRole("button", { name: /stop|end|finish/i }).first();
                    const starting = page.locator("text=/Starting|Preparing/i");
                    return (
                        (await stop.isVisible().catch(() => false)) ||
                        (await starting.isVisible().catch(() => false))
                    );
                },
                { timeout: 20_000 },
            )
            .toBeTruthy();
    });
});
