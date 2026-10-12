# WattTally iOS

Native SwiftUI client, following the Planini/Tracy XcodeGen structure.

- Bundle ID and SKU: `de.malaber.watttally`
- Apple team: `VWKG94374J`
- App Store record: [WattTally EV Charging](https://appstoreconnect.apple.com/apps/6821817316/distribution), Apple ID `6821817316`
- Home-screen name: WattTally; the shorter store name was unavailable.
- iOS/iPadOS 17+, app version 0.2.0 build 1.

## Build

```sh
xcodegen generate --spec ios/WattTallyIOS/project.yml
open ios/WattTallyIOS/WattTallyApp.xcodeproj
```

Select the WattTally scheme and your signing team/device in Xcode. Simulator builds
need no provisioning. CI generates the project and runs native tests. Archive and
upload through Xcode Organizer after backend rollout and device passkey testing.
The App Store record is a draft; no binary was uploaded or submitted for review.

## Backend dependency

Deploy the backend from this PR before signing in. Existing 0.1.0 servers do not
have the mobile authorization endpoints. Default: https://watttally.malaber.de.
Use an HTTPS test/custom server from the welcome screen; browser passkeys stay
bound to that server. No Associated Domains entitlement is required because
ASWebAuthenticationSession performs the WebAuthn ceremony on the server origin.

The browser displays an explicit approval screen. A two-minute one-use code is
bound to an S256 PKCE verifier and random state, then exchanged for an opaque
30-day session stored in device-only Keychain. Backend stores only its hash.
Logout revokes it; administrator account disabling revokes all sessions.
Redirects never forward credentials. Pending accounts cannot authorize a phone.

## Included

Homes and cars overview, automatic charging records, visible unmapped/ambiguous
assignments, explicit assignment confirmation, dated monthly report creation and
native PDF/XLSX/CSV sharing. Employer exports remain server-generated without
solar details. Refresh on launch, foreground and pull-to-refresh. Configure homes,
entities, tariffs and account administration through the linked server UI.

APNs/background notifications are not implemented. The app does not request
notification, location or Home Assistant control permissions. No deletion feature
was added. App Store metadata, privacy review, screenshots and device testing are
still required before public submission.

## Artwork

`design/mark.svg` is the editable WT/lightning logo. `scripts/render_icon.swift`
produces the opaque 1024px icon. Regenerate from the repository root:

```sh
swift scripts/render_icon.swift ios/WattTallyIOS/Assets.xcassets/AppIcon.appiconset/AppIcon.png
cp ios/WattTallyIOS/Assets.xcassets/AppIcon.appiconset/AppIcon.png ios/WattTallyIOS/Assets.xcassets/Brand.imageset/Brand.png
```
