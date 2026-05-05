import { withAuth } from "next-auth/middleware";
import { NextResponse } from "next/server";

/**
 * NextAuth Middleware
 * Protects all routes except login and public assets
 */
export default withAuth(
  function middleware(req) {
    return NextResponse.next();
  },
  {
    callbacks: {
      authorized: ({ req, token }) => {
        if (process.env.PLAYWRIGHT_TESTING === "true") return true;
        return !!token;
      },
    },
    pages: {
      signIn: "/login",
    },
  }
);

// Protect all routes except these
export const config = {
  matcher: [
    /*
     * Match all request paths except:
     * - api/auth (auth API routes)
     * - login (login page)
     * - _next/static (static files)
     * - _next/image (image optimization)
     * - favicon.ico (favicon)
     * - public files (images, etc)
     */
    "/((?!api/auth|login|_next/static|_next/image|favicon.ico|.*\\.png$|.*\\.svg$|audio-processor\\.worklet\\.js).*)",
  ],
};
