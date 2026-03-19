# Authentication Implementation

## Overview
Meeting Co-Pilot uses **Google OAuth** for secure authentication. Access can be restricted to approved email domains via the `ALLOWED_DOMAINS` environment variable.

> [!NOTE]
> For Role-Based Access Control (Permissions, Workspaces, Meeting Roles), please refer to **[RBAC_SPEC.md](./RBAC_SPEC.md)**.

---

## Authentication Architecture

```mermaid
sequenceDiagram
    participant U as User
    participant F as Frontend (NextAuth)
    participant G as Google OAuth
    participant B as Backend API (FastAPI)

    U->>F: Click "Sign in with Google"
    F->>G: Redirect to Google
    G->>U: Consent Screen
    U->>G: Approve
    G->>F: Auth Code -> Tokens
    F->>F: Validate Domain (if configured)
    F->>F: Create Session (JWT)
    
    Note over F,B: Authenticated Request
    F->>B: API Call + Bearer Token (ID Token)
    B->>G: Verify Token Signature (JKWS)
    B->>B: Extract User Info (Email)
    B->>B: Enforce RBAC (See RBAC_SPEC.md)
    B-->>F: Response
```

## Technology Stack

| Component | Technology | Purpose |
|-----------|------------|---------|
| **Frontend** | NextAuth.js v4 | Session management, OAuth flow |
| **Provider** | Google OAuth 2.0 | Identity Provider (IdP) |
| **Backend** | FastApi + python-jose | Token verification |
| **Protocol** | OIDC (OpenID Connect) | Standardization |

---

## Setup & Configuration

### 1. Google Cloud Console
1. Create a Project in GCP.
2. Configure OAuth Consent Screen (Internal/Private to Organization if possible).
3. Create OAuth 2.0 Credentials (Web Application).
4. Add Authorized Redirect URIs:
   - `http://localhost:3000/api/auth/callback/google`
   - `https://your-production-domain.com/api/auth/callback/google`

### 2. Environment Variables

#### Frontend (`.env.local`)
```bash
GOOGLE_CLIENT_ID=your-client-id
GOOGLE_CLIENT_SECRET=your-client-secret
NEXTAUTH_URL=http://localhost:3000 # or production URL
NEXTAUTH_SECRET=openssl-generated-secret
ALLOWED_DOMAINS=example.com,example.org
```

#### Backend (`.env`)
```bash
GOOGLE_CLIENT_ID=your-client-id # For validation
ALLOWED_DOMAINS=example.com,example.org
ADMIN_EMAILS=admin@example.com,ops@example.com
```

---

## Security Measures

1.  **Domain Restriction**: Frontend and backend can enforce an env-configured allowlist of email domains via `ALLOWED_DOMAINS`.
2.  **Stateless Validation**: Backend verifies JWT signature using Google's public keys (JWKS) on every request. No database session lookups required for validity check (though RBAC requires DB).
3.  **Audience Check**: Backend ensures token was issued specifically for this `GOOGLE_CLIENT_ID`.
4.  **Admin Endpoints**: Admin-only routes require `ADMIN_EMAILS` to be configured and fail closed when it is missing.

---

## Testing

### Manual Verification
1. Open Login Page.
2. Sign in with an account from an allowed domain -> **Success**.
3. Sign in with an account outside `ALLOWED_DOMAINS` -> **Access Denied**.
4. Check Browser DevTools -> Application -> Cookies -> `next-auth.session-token` exists.

### API Parsing
The backend extracts `user_id` (email) from the token. This `user_id` is then passed to the permissions layer.
