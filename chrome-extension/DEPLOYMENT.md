# Pnyx Extension — Production Deployment (Packed .crx + Workspace force-install)

This guide takes the extension from "works on my laptop" to "auto-installed on
every @appointy.com Chrome browser."

## Fixed identity (already set up)

The extension now has a permanent identity baked into `manifest.json` via the
`key` field, so the Extension ID is the same on every machine:

```
Extension ID:  gekbhloihkdompdhahaiomdgnpkhbfei
```

The matching **private signing key** is at the repo root: `pnyx-extension-key.pem`
(kept OUTSIDE the extension folder so it's never packed into the .crx). It is
gitignored — **never commit it, never lose it.** Whoever holds it can ship
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

**Option B — CLI (PKCS#8 key required)**
```bash
# From the repo root. Key must be PKCS#8 PEM (already converted).
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --pack-extension="$PWD/chrome-extension" \
  --pack-extension-key="$PWD/pnyx-extension-key.pem" \
  --no-message-box
```

Verify the packed ID matches `gekbhloihkdompdhahaiomdgnpkhbfei` and the file
starts with the `Cr24` magic.

---

## Hosting + force-install via Google Workspace

Force-install needs the `.crx` and `update_manifest.xml` reachable over public
HTTPS (Chrome's updater fetches them with no auth).

> ⚠️ The production GCS bucket `bifrost-pnyx-storage-b50ae8c5` CANNOT host these:
> it has Public Access Prevention enforced and UBLA locked. Use a public host.

**Recommended free host — a public GitHub repo** (CDN-backed raw URLs):

1. A prepared, ready-to-push folder lives at the repo root: `extension-dist/`
   (contains `pnyx-extension.crx`, `update_manifest.xml`, `push.sh`).
2. Create an **empty PUBLIC** GitHub repo named `pnyx-extension-dist`.
3. From `extension-dist/`, run `bash push.sh <github-owner>`.
4. The force-install update URL is then:
   `https://raw.githubusercontent.com/<owner>/pnyx-extension-dist/main/update_manifest.xml`

**Then in Google Admin console:**
5. **Devices → Chrome → Apps & extensions → Users & browsers**
6. Select the appointy.com org unit → **Add → Add Chrome app or extension by ID**
7. Extension ID: `gekbhloihkdompdhahaiomdgnpkhbfei`
   Installation URL: the raw `update_manifest.xml` URL from step 4
8. Set policy to **Force install**.

**Releasing an update:** bump `version` in `manifest.json`, re-pack, replace
`pnyx-extension.crx` + the `version` in `extension-dist/update_manifest.xml`,
then `git commit -am … && git push` in the dist repo.

> Alternative: **Chrome Web Store Unlisted** — no hosting, auto-updates, private
> link. One-time $5 dev account + review. Note: the store assigns its own
> Extension ID, so you'd re-point the OAuth client's Application ID to it once.

---

## Smoke test after deploy

1. On a fresh @appointy.com Chrome profile, confirm the extension auto-installs.
2. Click it → Connect Google Calendar (one consent, Internal = no warning).
3. Today's meetings load.
4. With an online meeting where the Pnyx bot is recording, confirm it shows
   **● Bot** (not a Start button). Otherwise it shows the muted manual Start.
5. Backend reachability: right-click popup → Inspect → Console. No
   `[Pnyx] active-bot-sessions failed` errors means the backend channel is live.
