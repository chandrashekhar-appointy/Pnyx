import { expect, test } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { stubAuthSession, stubBackend } from "./_helpers";

const ROUTES = [
    { name: "Home", path: "/" },
    { name: "Login", path: "/login" },
    { name: "Shared notes", path: "/shared-notes" },
];

test.describe("Accessibility (axe-core)", () => {
    test.beforeEach(async ({ page }) => {
        await stubAuthSession(page, { email: "test@appointy.com", name: "Test User" });
        await stubBackend(page);
    });

    for (const route of ROUTES) {
        test(`${route.name} has no critical violations`, async ({ page }) => {
            await page.goto(route.path, { waitUntil: "domcontentloaded" });

            const result = await new AxeBuilder({ page })
                .withTags(["wcag2a", "wcag2aa"])
                .analyze();

            const critical = result.violations.filter((v) => v.impact === "critical");
            if (critical.length > 0) {
                console.log(
                    "Critical a11y violations:",
                    JSON.stringify(critical, null, 2),
                );
            }
            expect(critical, "no critical a11y violations").toHaveLength(0);

            // Total violation count is logged but does not fail the build.
            test.info().annotations.push({
                type: "axe-violations",
                description: `${route.name}: ${result.violations.length} (${critical.length} critical)`,
            });
        });
    }
});
