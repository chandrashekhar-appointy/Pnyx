# Pnyx Extension — Production Deployment (Packed .crx + Workspace force-install)

This guide takes the extension from "works on my laptop" to "auto-installed on
every @appointy.com Chrome browser."

## Fixed identity (already set up)

The extension now has a permanent identity baked into `manifest.json` via the
`key` field, so the Extension ID is the same on every machine:

```
Extension ID:  gekbhloihkdompdhahaiomdgnpkhbfei
```

The matching **private signing key** is at `chrome-extension/pnyx-extension-key.pem`.
It is gitignored — **never commit it, never lose it.** Whoever holds it can ship
updates. Back it up somewhere safe (password manager / secret store).

## Production endpoints (already wired)

| What | URL |
|---|---|
| Frontend (opens for recording) | `https://frontend-dev-350906.bifrost.saastack.site` |
| Backend (bot-status, recent meetings) | `https://pnyx-dev-206432.bifrost.saastack.site` |

Both are in `manifest.json` host_permissions and `popup.js`.

---

## One-time setup (do these once)

### 1. Update the OAuth client to the fixed Extension ID

The Chrome App OAuth client must point at the permanent ID, not the old
per-machine one.

1. [Google Cloud Console → Credentials](https://console.cloud.google.com/apis/credentials)
2. Open OAuth client `352725372499-0c4qjkce9i3lf3sfd0bij8cge750dh3r`
3. Set **Application ID** = `gekbhloihkdompdhahaiomdgnpkhbfei`
4. Save

### 2. Set the OAuth consent screen to Internal

So the whole domain can use it and the Calendar scope needs no Google review.

1. Console → **OAuth consent screen**
2. User type → **Internal** → Save
   (Requires the project to live in the appointy.com Google Workspace org.)

### 3. Set the backend allowlist env in production

The deployed backend must accept the extension's access token. Add to the
production backend environment:

```
GOOGLE_CLIENT_ID=352725372499-0g67at0nlb0ium5vhgosn623snkupqij.apps.googleusercontent.com
EXTENSION_OAUTH_CLIENT_IDS=352725372499-0c4qjkce9i3lf3sfd0bij8cge750dh3r.apps.googleusercontent.com
```

(The `GOOGLE_CLIENT_ID` must match the web frontend's client, and
`EXTENSION_OAUTH_CLIENT_IDS` is the Chrome App client. Both are required for
auth to work end-to-end.)

---

## Packing the .crx

Each time you change the extension and bump `version` in `manifest.json`:

**Option A — Chrome UI**
1. `chrome://extensions` → **Pack extension**
2. Extension root: the `chrome-extension/` folder
3. Private key file: `chrome-extension/pnyx-extension-key.pem`
4. Produces `chrome-extension.crx`

**Option B — CLI**
```bash
# From the repo root
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --pack-extension="$PWD/chrome-extension" \
  --pack-extension-key="$PWD/chrome-extension/pnyx-extension-key.pem"
```

Verify the packed ID matches `gekbhloihkdompdhahaiomdgnpkhbfei`.

---

## Hosting + force-install via Google Workspace

Force-install needs the `.crx` and an `update_manifest.xml` reachable over HTTPS
(e.g. a GCS bucket or any static host).

1. Bump `manifest.json` `version` for every release (e.g. `1.0.0` → `1.0.1`).
2. Upload `chrome-extension.crx` to your host.
3. Edit `update_manifest.xml` (in this folder) so `codebase` points at the
   uploaded `.crx` URL and `version` matches the manifest. Upload it too.
4. Google Admin console → **Devices → Chrome → Apps & extensions → Users & browsers**
5. Select the appointy.com org unit → **Add → Add Chrome app or extension by ID**
6. Extension ID: `gekbhloihkdompdhahaiomdgnpkhbfei`
   Installation URL (your hosted update XML), e.g.
   `https://your-host/pnyx/update_manifest.xml`
7. Set policy to **Force install**.

Chrome on every signed-in @appointy.com browser will install it within minutes
and keep it updated whenever you bump the version + re-upload.

> Simpler alternative: publish to the **Chrome Web Store as Unlisted/Private**,
> then force-install by the store ID instead of self-hosting the XML. Skips the
> hosting + update-manifest steps but adds a one-time $5 dev account and review.

---

## Smoke test after deploy

1. On a fresh @appointy.com Chrome profile, confirm the extension auto-installs.
2. Click it → Connect Google Calendar (one consent, Internal = no warning).
3. Today's meetings load.
4. With an online meeting where the Pnyx bot is recording, confirm it shows
   **● Bot** (not a Start button). Otherwise it shows the muted manual Start.
5. Backend reachability: right-click popup → Inspect → Console. No
   `[Pnyx] active-bot-sessions failed` errors means the backend channel is live.
