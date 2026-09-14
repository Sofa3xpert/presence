# Presence for Mac — packaging

What ships: `Presence.app` inside `Presence-<version>-arm64.dmg`, for Macs with
Apple silicon (M1 and later) on macOS 11 or newer. Double-click the app and
Presence appears as a small bracket-and-sprout icon in the menu bar; it starts
the local Presence pages on your own computer (only your Mac can reach them)
and opens them in your browser. The icon's menu has **Open Presence**,
**Open data folder** and **Quit Presence**. Nothing runs in a terminal and
nothing leaves your computer.

Your settings and tracker live in `~/Library/Application Support/Presence`;
anything the app prints goes to `~/Library/Logs/Presence.log`.

## What people see with an unsigned build (macOS 15 and 26)

Until the app is notarized with Apple, the first launch is bumpy. This is the
exact experience, in plain words — it is also the text for the download page:

1. Open the `.dmg` and drag **Presence** into **Applications**.
2. Double-click Presence. macOS shows *"Presence" Not Opened — Apple could not
   verify "Presence" is free of malware*. Click **Done** (not Move to Trash).
3. Open **System Settings → Privacy & Security**, scroll down to the
   **Security** section. It says *"Presence" was blocked to protect your Mac*.
   Click **Open Anyway**. (The button is only there for about an hour after the
   attempt in step 2; if it is gone, double-click Presence again.)
4. Confirm **Open Anyway** in the dialog and enter your Mac password.
5. Presence opens. macOS remembers the choice; from now on it opens normally.

On macOS 14 (Sonoma) it is shorter: Control-click Presence in Applications,
choose **Open**, then **Open** again.

If macOS instead says *"Presence" is damaged and can't be opened*, the download
was marked as quarantined and the signature is not trusted. This is rare; the
fix needs Terminal (`xattr -d com.apple.quarantine /Applications/Presence.app`)
— which is exactly what we do not want to ask of anyone, hence notarization.

## What notarization needs

To make the first launch a plain double-click, Apple must notarize the app:

- an **Apple Developer Program** membership — 99 USD per year, under the
  maintainer's legal name;
- a **Developer ID Application** certificate exported as a `.p12` file;
- an **app-specific password** for the Apple ID used for notarization.

The release workflow does the rest automatically when these repository
secrets exist (it skips signing when they do not):

| secret | value |
| --- | --- |
| `MACOS_CERT_P12_BASE64` | the `.p12` file, base64-encoded |
| `MACOS_CERT_PASSWORD` | the password chosen when exporting the `.p12` |
| `APPLE_ID` | the Apple ID e-mail of the developer account |
| `APPLE_TEAM_ID` | the 10-character team id |
| `APPLE_APP_PASSWORD` | the app-specific password |

The identity is read from the certificate, the app is signed with the hardened
runtime and `entitlements.plist`, the `.dmg` is signed, submitted with
`notarytool --wait` and stapled. A notarized build passes
`spctl --assess --type execute` as *Notarized Developer ID*.

Intel Macs are not built: `cryptography` stopped publishing x86_64 macOS wheels
in 49.0, so a universal build is impossible with the current dependencies.

## Building

```
scripts/build_mac.sh            # uv sync --group macapp, PyInstaller, smoke test, dmg
SKIP_SYNC=1 scripts/build_mac.sh  # reuse the current venv
```

Outputs `dist/Presence.app` and `dist/Presence-<version>-arm64.dmg`; the
version is `presence.__version__`. Measured on an M-series Mac: about 30–45 s,
app 104 MB (PyMuPDF is half of it), dmg 54 MB.

Pieces:

- `launcher.py` — entry point: logs to `~/Library/Logs/Presence.log`, sets up
  the data folder on first run, picks a free port (8790 preferred; if a
  Presence already answers there it just opens the browser to it), runs Flask
  in a background thread and the menu bar on the main thread.
  `PRESENCE_SMOKE=1 dist/Presence.app/Contents/MacOS/Presence` runs a headless
  self-test that prints `SMOKE OK`. `PRESENCE_PORT` overrides the port.
- `Presence.spec` — PyInstaller spec: onedir, windowed bundle, `LSUIElement`
  (menu-bar only), bundle id `org.presence.app`, templates and static files as
  data, signing only when `CODESIGN_IDENTITY` is set. Never onefile, never
  universal2, never UPX.
- `entitlements.plist` — hardened-runtime exceptions a Python app needs.
- `make_dmg.sh` — `hdiutil` (compressed, Applications shortcut, retries,
  verify).
- `Presence.svg` / `menubar.svg` — icon sources; `make_icons.py` regenerates
  `Presence.icns` and the menu-bar template glyph `menubar.png`.

CI: `.github/workflows/release.yml` builds on a `macos-15` runner on every
`v*` tag (and on demand), uploads the dmg as an artifact and attaches it to the
GitHub release.
