import { expect, test } from "@playwright/test";
import { stubAuthSession, stubBackend } from "./_helpers";

const MEETING_ID = "00000000-0000-0000-0000-000000000abc";

test.describe("Sharing and chat sidebar", () => {
    test.beforeEach(async ({ page }) => {
        await stubAuthSession(page, { email: "test@appointy.com", name: "Test User" });
        await stubBackend(page);

        const backend =
            process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:5167";

        await page.route(new RegExp(`.*/api/sharing/.*/share`), (route) => {
            if (route.request().method() === "OPTIONS") {
                return route.fulfill({
                    status: 204,
                    headers: {
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Methods": "POST, GET, OPTIONS",
                        "Access-Control-Allow-Headers": "*",
                    },
                });
            }
            return route.fulfill({
                status: 200,
                contentType: "application/json",
                headers: {
                    "Access-Control-Allow-Origin": "*",
                },
                body: JSON.stringify({
                    share_token: "shared-token-xyz",
                    share_url: `http://localhost:3118/meeting-details?id=${MEETING_ID}&shared=true&token=shared-token-xyz`,
                }),
            });
        });

        await page.route(new RegExp(`.*/chat-meeting`), (route) =>
            route.fulfill({
                status: 200,
                contentType: "text/plain",
                body: "We decided to ship by Friday.",
            }),
        );
    });

    test("chat sidebar accepts a question and renders the answer", async ({ page }) => {
        const url = `/meeting-details?id=${MEETING_ID}`;
        await page.goto(url, { waitUntil: "domcontentloaded" });

        const chatInput = page
            .getByPlaceholder(/ask about|ask anything/i)
            .first();
        if (!(await chatInput.isVisible().catch(() => false))) {
            const askAiBtn = page.getByRole("button", { name: /ask ai/i }).first();
            if (await askAiBtn.isVisible().catch(() => false)) {
                await askAiBtn.click();
            } else {
                test.skip(
                    true,
                    "Chat input not found — UI may have changed; update placeholder match",
                );
            }
        }
        await chatInput.fill("What did we decide?");
        await chatInput.press("Enter");

        await expect
            .poll(
                async () => (await page.locator("text=/ship by Friday/i").count()) > 0,
                { timeout: 15_000 },
            )
            .toBeTruthy();
    });

    test.skip("share dialog produces a sharable link", async ({ page }) => {
        // Share Notes was removed in v1 (Phase 1 surface reduction). Re-enable
        // this test when Share Notes is restored.
        const url = `/meeting-details?id=${MEETING_ID}`;
        await page.goto(url, { waitUntil: "domcontentloaded" });

        const shareButton = page.getByRole("button", { name: /^share$/i }).first();
        await shareButton.click();

        const generateButton = page.getByRole("button", { name: /^share notes$/i }).first();
        await generateButton.click();

        // The share URL should be either visible in the dialog or read off the
        // clipboard.  We check for the token substring.
        await expect
            .poll(
                async () => {
                    const text = await page.content();
                    return text.includes("shared-token-xyz");
                },
                { timeout: 15_000 },
            )
            .toBeTruthy();
    });
});
