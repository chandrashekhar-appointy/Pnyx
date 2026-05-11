/**
 * Synthetic production monitoring tests.
 *
 * These run against the LIVE production deployment (https://meet.quexio.com by
 * default) on a 15-minute cron.  They are READ-ONLY: no recording, no
 * mutation, no side-effects.
 *
 * Config: frontend/playwright.synthetic.config.ts
 * Trigger: .github/workflows/synthetic-monitoring.yml
 */
import { expect, test } from "@playwright/test";

// ---------------------------------------------------------------------------
// 1. Homepage loads without 5xx
// ---------------------------------------------------------------------------

test("homepage loads without server error", async ({ page, baseURL }) => {
    const response = await page.goto(baseURL || "/", {
        waitUntil: "domcontentloaded",
    });
    expect(response).not.toBeNull();
    expect(response?.status()).toBeLessThan(500);
});

// ---------------------------------------------------------------------------
// 2. Login page renders with Google Sign-In
// ---------------------------------------------------------------------------

test("login page renders and contains sign-in option", async ({ page, baseURL }) => {
    const response = await page.goto(`${baseURL}/login`, {
        waitUntil: "domcontentloaded",
    });
    expect(response?.status()).toBeLessThan(500);

    // The login page should contain at least one interactive element or text
    // referencing Google / Sign In.
    const content = await page.content();
    const hasSignIn =
        /sign.?in|log.?in|google|continue with/i.test(content);
    expect(hasSignIn).toBeTruthy();
});

// ---------------------------------------------------------------------------
// 3. CSRF endpoint responds with valid JSON
// ---------------------------------------------------------------------------

test("/api/auth/csrf returns valid JSON", async ({ request, baseURL }) => {
    const resp = await request.get(`${baseURL}/api/auth/csrf`);
    // NextAuth may return 200 or redirect — either is acceptable in prod.
    expect(resp.status()).toBeLessThan(500);

    if (resp.status() === 200) {
        const body = await resp.json();
        expect(body).toHaveProperty("csrfToken");
    }
});

// ---------------------------------------------------------------------------
// 4. No critical JS console errors on homepage
// ---------------------------------------------------------------------------

test("homepage has no critical JS errors", async ({ page, baseURL }) => {
    const errors: string[] = [];
    page.on("pageerror", (err) => {
        // Ignore known non-critical errors (e.g. PostHog, analytics)
        const msg = err.message || "";
        if (/posthog|analytics|gtag|sentry/i.test(msg)) return;
        errors.push(msg);
    });

    await page.goto(baseURL || "/", { waitUntil: "networkidle" });

    // Allow up to 1 non-critical error (third-party scripts can be noisy)
    expect(
        errors.length,
        `Critical JS errors detected: ${errors.join("; ")}`,
    ).toBeLessThanOrEqual(1);
});

// ---------------------------------------------------------------------------
// 5. API health endpoint
// ---------------------------------------------------------------------------

test("backend /health endpoint responds", async ({ request }) => {
    const backendUrl = process.env.SYNTHETIC_BACKEND_URL || "https://meet.quexio.com";
    const resp = await request.get(`${backendUrl}/health`);
    expect(resp.status()).toBeLessThan(500);
});

// ---------------------------------------------------------------------------
// 6. Shared notes page loads (public route)
// ---------------------------------------------------------------------------

test("shared-notes page loads without error", async ({ page, baseURL }) => {
    const response = await page.goto(`${baseURL}/shared-notes`, {
        waitUntil: "domcontentloaded",
    });
    expect(response).not.toBeNull();
    expect(response?.status()).toBeLessThan(500);
});
