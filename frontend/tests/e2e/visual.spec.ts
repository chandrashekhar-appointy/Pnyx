import { expect, test } from "@playwright/test";
import { stubAuthSession, stubBackend } from "./_helpers";

const ROUTES = [
    { name: "home", path: "/" },
    { name: "login", path: "/login" },
];

test.describe("Visual regression snapshots", () => {
    test.beforeEach(async ({ page }) => {
        await stubAuthSession(page, { email: "test@appointy.com", name: "Test User" });
        await stubBackend(page);
    });

    for (const route of ROUTES) {
        test(`${route.name} renders consistently`, async ({ page }) => {
            await page.goto(route.path, { waitUntil: "networkidle" });

            // Hide volatile pixels: anything driven by Date.now(), animations,
            // or LLM-generated text.
            await page.addStyleTag({
                content: `
                    .timestamp,
                    [data-volatile],
                    [data-testid="timestamp"] {
                        visibility: hidden !important;
                    }
                    *, *::before, *::after {
                        animation-duration: 0s !important;
                        transition-duration: 0s !important;
                    }
                `,
            });

            await expect(page).toHaveScreenshot(`${route.name}.png`, {
                fullPage: true,
                maxDiffPixelRatio: 0.02,
            });
        });
    }
});
