# Google sign-in: the one-time console setup

Presence's packaged app signs people into Google with **one click**. That works
because the release build carries its own Google OAuth client (a "Desktop app"
client) that only ever asks for the narrow `drive.file` permission: Presence can
see and edit the sheet it creates for the person, and nothing else in their
Drive. Google classes that scope as **non-sensitive**, so the app needs no
security review to be used by anyone.

This page is for the maintainer. It has three parts: what the person sees, the
console clicks (done once), and the facts behind the choices, with the pages
they were checked against on 2026-09-14.

## What the person sees

1. Setup → step 3 "Tracker" → choose "Also in a Google Sheet" → one button,
   **Connect Google**, with one sentence: *Presence can only see the sheet it
   creates for you — nothing else in your Google Drive.*
2. Their browser opens on accounts.google.com. They pick an account. The consent
   screen says: *Presence wants to: See, edit, create, and delete only the
   specific Google Drive files you use with this app* → Continue.
3. The browser shows *Presence is connected to Google. You can close this tab
   and go back to Presence.* The reply lands on `127.0.0.1` on a random port
   (Google's recommended "loopback" flow for desktop apps); nothing leaves the
   machine except the sign-in itself.
4. Presence creates a sheet called **Presence tracker** in their Drive, fills it
   with the tracker rows straight away and shows *Open your sheet*, *Sync now*
   and the last-sync line. Sync also runs at the end of every cycle.
5. If Google later withdraws the token (the person revoked access, or the token
   went unused for six months), the page shows *Google needs you to reconnect*
   and a **Reconnect Google** button. Nothing is lost; the sheet stays.

No uploads, no console, no file names. Until brand verification is done (see
below), the consent screen shows the project's support email / domain instead
of a logo — that is the only cosmetic difference.

**Source builds** (someone running Presence from a git checkout) have no client
inside. Step 3 then says *Google sign-in isn't included in this build* and a
collapsed **Advanced** block holds the two developer paths, unchanged: upload
your own Desktop-app client file, or a service-account key. "Connect an empty
sheet you made" and "import rows from a sheet link" are only offered on the
service-account path — under `drive.file` Google will not let the app read a
sheet it did not create. Everyone can still bring an old tracker in as a CSV
file.

## The console clicks (once)

Labels are the ones the Google Workspace guides use as of 2026-09-03.

1. **Project.** console.cloud.google.com → IAM & Admin → **Create a Project** →
   name `Presence` → Create. No billing account is needed: standard use of the
   Sheets and Drive APIs is "available at no additional cost".
2. **APIs.** APIs & Services → **Library** → enable **Google Sheets API** and
   **Google Drive API** (Drive is what lets the app create the sheet file; the
   Sheets API alone only reads and writes sheets that already exist).
3. **Branding.** Google Auth platform → **Branding**: App name `Presence`,
   User support email = your Gmail, **App logo: leave empty** (a logo triggers
   brand verification), App domain / homepage / privacy policy / terms = pages
   on a domain you own if you have one (github.io is reported not to be
   accepted as an authorized domain), Developer contact = your email, accept
   the Google API Services User Data Policy → Save.
4. **Audience.** Google Auth platform → **Audience** → User type **External**.
   Leave Test users empty. Press **Publish app** and confirm. (Left in
   "Testing", the app would be capped at 100 users and every sign-in would
   expire after seven days.)
5. **Data access.** Google Auth platform → **Data Access** → **Add or Remove
   Scopes** → tick only `.../auth/drive.file` (listed under Non-sensitive) →
   Update → Save. Do not add any sensitive or restricted scope: that would put
   the "unverified app" screen in front of every person and require a full
   security review.
6. **Client.** Google Auth platform → **Clients** → **Create Client** →
   Application type **Desktop app** → Name `Presence desktop` → Create →
   **Download JSON immediately**. Since June 2025 the secret is shown and
   downloadable only at creation; afterwards Google stores a hash. If it is
   lost, use the client's *rotate secret* action to get a new one.
7. **Store it.** In the GitHub repository → Settings → Secrets and variables →
   Actions → New repository secret, twice:
   `PRESENCE_GOOGLE_CLIENT_ID` = the `client_id` value,
   `PRESENCE_GOOGLE_CLIENT_SECRET` = the `client_secret` value.
   Then delete the downloaded JSON. Never commit it; the test suite greps the
   tree for anything that looks like a client secret.
8. **Optional, later: brand verification.** Verification Center → Verify
   Branding, after verifying your domain in Search Console and hosting a
   homepage plus a privacy policy on that same domain, linked from the consent
   screen. The automated check takes minutes; a manual one 2–3 business days.
   Result: the consent screen shows "Presence" with a logo instead of the
   domain only. Optional for apps that use non-sensitive scopes only.

Calendar notes:

- **Unused clients are deleted.** An OAuth client with no token request and no
  settings change for **6 months** is deleted automatically (restorable for 30
  days). Any real use keeps it alive; if the app sits unreleased for half a
  year, sign in once from a build to keep the client.
- **Quota billing is coming.** Google has announced that, later in 2026 and
  after 90 days' notice, usage above the standard daily thresholds will be
  charged and quota increases will need a billing account. Presence stays far
  below the thresholds (see the numbers below), but keep an eye on
  developers.google.com/workspace/tools-safety and the project's Quotas page.

## How the release build gets the client

The two values live in `src/presence/adapters/google_client.py` as the
placeholders `__PRESENCE_GOOGLE_CLIENT_ID__` and
`__PRESENCE_GOOGLE_CLIENT_SECRET__`. The release workflow runs

    python scripts/inject_google_client.py

with the two repository secrets in its environment, right before packaging.
The script rewrites just those two assignments, is safe to run twice, exits 2
if a value is missing, and prints nothing secret. `--check` reports whether a
client is in place. For a local try-out, set the same two variables in the
environment instead — the module reads them first.

The client secret ends up inside the distributed app. That is by design:
Google's OAuth overview says of installed applications that the client secret
"is obviously not treated as a secret", and its policy says only that
credentials must never be committed to a public repository. Sign-in still uses
PKCE (on by default in google-auth-oauthlib), but Google's Desktop client type
expects the secret at token exchange in practice, so it is sent.

## Quota and how Presence stays inside it

Every install shares the one project's quota:

- Sheets API: **300 read + 300 write requests per minute per project**, and
  60 + 60 per minute per user.
- Drive API (projects created after 1 May 2026): 1,000,000 units per minute
  per project; a read costs 5 units, an edit 50, a list 100.

A sync is a fixed three Sheets calls: one read of the whole tab, one clear, one
write of all rows — never per row. Creating the sheet is one Sheets call. The
client retries when Google answers 429, or 403 with a rate-limit reason, with
exponential backoff plus jitter, at most five attempts, then tells the person
"Google is busy — try again in a minute".

The project-wide ceiling is the one to watch: 300 writes per minute means at
most ~150 installs may sync in the same minute. That is fine as long as syncs
are spread out — each person's cycle runs at their own chosen time — and the
retry absorbs the occasional collision. If the app ever gains a fixed default
cycle time, add a few random minutes per install so a thousand installs do not
sync at the same clock minute.

If the shared client is ever disabled or throttled beyond use, the fallback is
already in the app: the Advanced block lets a person use their own Google
client or a service account, and the tracker is always exportable as CSV.

## Facts these choices rest on (checked 2026-09-14)

- OAuth 2.0 overview — installed apps embed the client ID "and, in some cases,
  a client secret ... (In this context, the client secret is obviously not
  treated as a secret.)":
  https://developers.google.com/identity/protocols/oauth2
- OAuth for desktop apps (updated 2026-08-07) — loopback `http://127.0.0.1:port`
  on a random port is the recommended flow for macOS, Linux and Windows;
  custom URI schemes are no longer supported:
  https://developers.google.com/identity/protocols/oauth2/native-app
- Loopback stays supported for Desktop-app clients:
  https://developers.google.com/identity/protocols/oauth2/resources/loopback-migration
- Manual copy-paste (OOB) flow is gone since 31 Jan 2023:
  https://developers.google.com/identity/protocols/oauth2/resources/oob-migration
- Never commit client credentials to a public repository; one client per
  platform: https://developers.google.com/identity/protocols/oauth2/policies
- Client secrets visible only at creation since June 2025; inactive clients
  deleted after 6 months, restorable 30 days:
  https://developers.googleblog.com/usability-and-safety-updates-to-google-auth-platform/
  and https://support.google.com/cloud/answer/15549257
- Desktop clients need the secret at token exchange even with PKCE — developer
  forum reports (Jan and Aug 2026), not confirmed by Google's docs:
  https://discuss.google.dev/t/google-auth-platform-question-about-desktop-app-client-and-secret/178938
- `drive.file` is "Non-sensitive (Recommended)" for Sheets, wording of the
  consent line: https://developers.google.com/workspace/sheets/api/scopes and
  https://developers.google.com/workspace/drive/api/guides/api-specific-auth
- No app verification needed when only non-sensitive scopes are used; the
  name-and-logo display needs the lighter brand verification:
  https://support.google.com/cloud/answer/13463073
- Testing status = 100 users and 7-day tokens; Publish app opens it to any
  Google account: https://support.google.com/cloud/answer/15549945 and
  https://developers.google.com/identity/protocols/oauth2#expiration
- The unverified-app screen only appears for sensitive/restricted scopes:
  https://support.google.com/cloud/answer/7454865
- Brand verification requirements and timing; without it "users will only see
  your domain name listed on the consent page":
  https://developers.google.com/identity/protocols/oauth2/production-readiness/brand-verification
- Branding page says homepage/privacy/terms links are required for external
  production apps and that a logo triggers verification; whether Publish is
  blocked without those links for a non-sensitive-only app is not verified:
  https://support.google.com/cloud/answer/15549049
- github.io rejected as an authorized domain — community reports only:
  https://github.com/google-home/smart-home-nodejs/issues/450
- Sheets API limits (updated 2026-09-03):
  https://developers.google.com/workspace/sheets/api/limits
- Drive API limits (updated 2026-09-11) and the coming overage charges:
  https://developers.google.com/workspace/drive/api/guides/limits and
  https://developers.google.com/workspace/tools-safety
- Console labels (Google Auth platform → Branding / Audience / Data Access /
  Clients; Desktop app client type):
  https://developers.google.com/workspace/guides/configure-oauth-consent
- What happens to a shared client at scale — rclone retiring its embedded
  client because of the coming per-quota charges:
  https://forum.rclone.org/t/google-drive-and-google-photos-users-action-required/54005
- google-auth-oauthlib 1.4.1: PKCE on by default, `run_local_server` builds the
  `http://{host}:{port}/` redirect:
  https://github.com/googleapis/google-auth-library-python-oauthlib/blob/main/google_auth_oauthlib/flow.py
