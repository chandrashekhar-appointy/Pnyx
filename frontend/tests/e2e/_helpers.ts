import { Page, Route } from "@playwright/test";

/**
 * Stub a NextAuth session response so the frontend renders authenticated.
 * Apply at the start of every spec that needs auth.
 */
export async function stubAuthSession(
    page: Page,
    user: { email: string; name: string },
): Promise<void> {
    await page.route("**/api/auth/session", (route: Route) => {
        route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                user: { email: user.email, name: user.name, image: null },
                accessToken: "e2e-access-token",
                idToken: "e2e-id-token",
                expires: new Date(Date.now() + 3600_000).toISOString(),
            }),
        });
    });
    await page.route("**/api/auth/csrf", (route: Route) => {
        route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ csrfToken: "e2e-csrf" }),
        });
    });
    await page.route("**/api/auth/providers", (route: Route) => {
        route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                google: {
                    id: "google",
                    name: "Google",
                    type: "oauth",
                    signinUrl: "/api/auth/signin/google",
                    callbackUrl: "/api/auth/callback/google",
                },
            }),
        });
    });
}

/**
 * Stub the backend (FastAPI) endpoints the frontend calls during the happy
 * path so the suite does not require a running backend.  Each call returns a
 * deterministic JSON shape.  Specs may override individual routes.
 */
export async function stubBackend(page: Page): Promise<void> {
    const backend = process.env.NEXT_PUBLIC_BACKEND_URL || "http://localhost:5167";
    const meetingId = "00000000-0000-0000-0000-000000000abc";

    await page.route(`${backend}/get-meetings`, (route) =>
        route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ meetings: [] }),
        }),
    );

    await page.route(`${backend}/create`, (route) =>
        route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ meeting_id: meetingId, title: "E2E Meeting" }),
        }),
    );

    await page.route(`${backend}/api/credits`, (route) =>
        route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ balance: 9999, is_unlimited: false }),
        }),
    );

    await page.route(new RegExp(`${escapeRegex(backend)}/get-meeting/.*`), (route) =>
        route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                meeting_id: meetingId,
                title: "E2E Meeting",
                transcripts: [
                    { id: "t1", text: "Hello team", timestamp: "00:01" },
                ],
                summary: { key_decisions: [], action_items: [] },
            }),
        }),
    );

    await page.route(new RegExp(`${escapeRegex(backend)}/meetings/.*/recording-url`), (route) =>
        route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ url: `${backend}/recordings/${meetingId}/recording.wav` }),
        }),
    );
}

function escapeRegex(s: string): string {
    return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
