import { Page, Route } from "@playwright/test";

/**
 * Stub a NextAuth session so the frontend renders as authenticated.
 *
 * NextAuth middleware checks the session cookie before the page loads, so
 * route stubbing alone is too late — the middleware redirects to /login first.
 * We use addInitScript to inject sessionStorage flags the app's AuthProvider
 * reads as a bypass, AND stub all /api/auth/* routes so any client-side
 * session checks also see a valid response.
 */
export async function stubAuthSession(
    page: Page,
    user: { email: string; name: string },
): Promise<void> {
    // Injected before every navigation — sets the idToken localStorage entry
    // that authFetch reads for the Authorization header, and marks this as an
    // e2e run so AuthProvider skips the real NextAuth session check.
    await page.addInitScript(({ email, name }) => {
        const payload = btoa(JSON.stringify({ email, name, iat: Math.floor(Date.now() / 1000), exp: Math.floor(Date.now() / 1000) + 3600 }));
        const token = "e2e." + payload + ".e2e-sig";
        localStorage.setItem("e2e-id-token", token);
        localStorage.setItem("e2e-user-email", email);
        // Signal AuthProvider to treat this session as authenticated
        sessionStorage.setItem("e2e-authenticated", "true");
    }, user);

    // Still stub the network routes so client-side useSession() also resolves
    await page.route("**/api/auth/session", (route: Route) => {
        route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                user: { email: user.email, name: user.name, image: null },
                accessToken: "e2e-access-token",
                idToken: `e2e.${btoa(JSON.stringify({ email: user.email }))}.sig`,
                expires: new Date(Date.now() + 3600_000).toISOString(),
            }),
        });
    });
    await page.route("**/api/auth/csrf", (route: Route) => {
        route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ csrfToken: "e2e-csrf" }) });
    });
    await page.route("**/api/auth/providers", (route: Route) => {
        route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                google: { id: "google", name: "Google", type: "oauth", signinUrl: "/api/auth/signin/google", callbackUrl: "/api/auth/callback/google" },
            }),
        });
    });
    // Intercept the middleware's session check — return 200 with valid session
    await page.route("**/api/auth/**", (route: Route) => {
        if (route.request().url().includes("session")) {
            route.fulfill({
                status: 200,
                contentType: "application/json",
                body: JSON.stringify({
                    user: { email: user.email, name: user.name, image: null },
                    expires: new Date(Date.now() + 3600_000).toISOString(),
                }),
            });
        } else {
            route.continue();
        }
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

    // Credit balance — match the actual CreditBalanceResponse shape
    await page.route(`${backend}/api/credits`, (route) =>
        route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                weekly: 9000,
                admin: 0,
                purchased: 0,
                total: 9000,
                is_unlimited: false,
            }),
        }),
    );

    // Calendar + bot sessions — needed on the home page after Phase 4.1
    await page.route(`${backend}/api/calendar/status`, (route) =>
        route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ connected: false, provider: null }),
        }),
    );

    // Analytics — swallow events silently
    await page.route(`${backend}/analytics/track`, (route) =>
        route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ status: "success" }) }),
    );

    await page.route(new RegExp(`${escapeRegex(backend)}/get-meeting/.*`), (route) =>
        route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                id: meetingId,
                meeting_id: meetingId,
                title: "E2E Meeting",
                transcripts: [
                    { id: "t1", text: "Hello team", timestamp: "00:01" },
                ],
                summary: { markdown: "# Summary\nThis is the meeting summary." },
            }),
        }),
    );

    await page.route(new RegExp(`${escapeRegex(backend)}/get-summary/.*`), (route) =>
        route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({
                status: "completed",
                data: { markdown: "# Summary\nThis is the meeting summary." },
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

    await page.route(`${backend}/api/user/ai-host-styles`, (route) =>
        route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ styles: [], default_style_id: "system:facilitator" }),
        }),
    );

    await page.route(`${backend}/api/sharing/shared-with-me`, (route) =>
        route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify([]),
        }),
    );

    await page.route(`${backend}/api/meetings/active-bot-sessions`, (route) =>
        route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify([]),
        }),
    );

    await page.route(`${backend}/save-transcript`, (route) =>
        route.fulfill({
            status: 200,
            contentType: "application/json",
            body: JSON.stringify({ meeting_id: meetingId }),
        }),
    );
}

function escapeRegex(s: string): string {
    return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
