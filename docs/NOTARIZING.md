# Signing & notarizing releases

One-time setup plus the release command. Requires an Apple Developer
Program membership ($99/yr) and Xcode Command Line Tools.

## One-time setup

1. **Enroll** at [developer.apple.com](https://developer.apple.com/programs/enroll/)
   (personal enrollment is fine).

2. **Create a "Developer ID Application" certificate.** Easiest path:
   Xcode → Settings → Accounts → your Apple ID → Manage Certificates →
   "+" → **Developer ID Application**. (Or create it on the
   developer.apple.com Certificates page and double-click the download
   to install it in your keychain.)

3. **Find the identity's exact name:**

   ```bash
   security find-identity -v -p codesigning
   ```

   You want the line like
   `"Developer ID Application: Your Name (ABCDE12345)"`.

4. **Store notarization credentials** (once). Create an app-specific
   password at [appleid.apple.com](https://appleid.apple.com) →
   Sign-In and Security → App-Specific Passwords, then:

   ```bash
   xcrun notarytool store-credentials granola-sync \
       --apple-id you@example.com \
       --team-id ABCDE12345 \
       --password <app-specific-password>
   ```

   This saves a keychain profile named `granola-sync` — the password
   never appears in the repo or shell history again.

## Building a signed + notarized release

```bash
GS_SIGN_IDENTITY="Developer ID Application: Your Name (ABCDE12345)" \
GS_NOTARY_PROFILE="granola-sync" \
app/build.sh
```

The build script signs every binary with the hardened runtime and
[entitlements](../app/entitlements.plist) PyInstaller apps need,
submits the zip to Apple (`notarytool … --wait`, typically 1–5
minutes), staples the ticket to the app, and re-zips. The resulting
`app/dist/Granola-Sync.zip` opens on any Mac with no Gatekeeper
warnings — attach it to a GitHub Release.

## Verifying

```bash
APP="$HOME/Library/Caches/GranolaSyncBuild/dist/Granola Sync.app"
spctl --assess --type execute -vv "$APP"   # want: "accepted … Notarized Developer ID"
xcrun stapler validate "$APP"              # want: "The validate action worked!"
```

## Notes

- Without `GS_SIGN_IDENTITY`, `app/build.sh` still produces the
  ad-hoc-signed build (users right-click → Open once) — nothing about
  the default workflow changed.
- If notarization is rejected, get the log with
  `xcrun notarytool log <submission-id> --keychain-profile granola-sync`.
- `HTTP status code: 403. A required agreement is missing or has
  expired` happens before upload, so there's no submission ID or log. Apple
  has published an updated Developer Program License Agreement. The
  account holder must accept it at developer.apple.com/account (or App
  Store Connect → Business), then re-run the build. `build.sh` stops at
  that point, so the zip it left behind is signed but **not
  notarized**. Don't ship it.
  The usual culprits for PyInstaller apps are covered by
  `app/entitlements.plist` already.
