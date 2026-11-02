# Meta / Instagram setup guide

This guide connects the app to your Instagram **Creator** account using the official
**Instagram API with Instagram Login**. It is only needed once you are ready to test with a real
account — development and tests run fully mocked.

Meta's dashboard changes often; if a screen looks different, check the official docs:
<https://developers.facebook.com/docs/instagram-platform>

## 1. Prerequisites

- An Instagram **Creator** (or Business) account.
- A Meta developer account: <https://developers.facebook.com/>.
- A public **HTTPS** URL for the app (a deployed server, or a tunnel such as ngrok during
  development). Meta cannot deliver webhooks to `localhost`.

## 2. Create the Meta app

1. Go to **My Apps → Create App**.
2. Choose the Instagram use case (manage messaging and content on Instagram).
3. Open **App settings → Basic** and copy:
   - App ID → `META_APP_ID`
   - App secret → `META_APP_SECRET`

## 3. Connect your Instagram account

1. In the app dashboard open **Instagram → API setup with Instagram business login**.
2. Add your Instagram account (while the app is in Development mode, the account must have a role
   on the app, e.g. Instagram tester).
3. Generate an access token for the account and note its Instagram user ID:
   - `INSTAGRAM_USER_ID`
   - `INSTAGRAM_ACCESS_TOKEN` (long-lived tokens last ~60 days and can be refreshed)

## 4. Permissions

Request these permissions:

| Permission | Used for |
| --- | --- |
| `instagram_business_basic` | Read profile and media |
| `instagram_business_manage_comments` | Read comments, reply to comments |
| `instagram_business_manage_messages` | Private replies and DMs (including images) |

Not needed: `instagram_business_content_publish` (the app never publishes posts) and the
`HUMAN_AGENT` message tag (the app stays within the standard 24-hour window).

## 5. Webhooks

1. Pick a long random string and set it as `META_WEBHOOK_VERIFY_TOKEN`
   (e.g. `openssl rand -hex 32`).
2. In **Instagram → Webhooks**, set:
   - Callback URL: `https://<your-domain>/webhook` (endpoint added in Step 2)
   - Verify token: the same value as `META_WEBHOOK_VERIFY_TOKEN`
3. Subscribe to the `comments` and `messages` fields.

The app verifies every webhook request with the `X-Hub-Signature-256` header using
`META_APP_SECRET`.

## 6. Allow message access on Instagram

In the Instagram app: **Settings → Messages and calls → Connected tools → Allow access to
messages**. Without this, DM features will not work.

## 7. Going live

In Development mode, only accounts with a role on the app generate webhook events. To receive
comments and messages from the public you will likely need to:

- Complete **App Review** for the permissions above (Advanced Access).
- Complete business verification if Meta asks for it.
- Switch the app to **Live** mode.

## Instagram messaging rules (enforced by Meta)

- **Private replies:** you can send **one** private DM in response to a comment, within
  **7 days** of the comment.
- **24-hour window:** after a user messages you, you can reply freely for 24 hours. You cannot
  send unsolicited (cold) DMs.
- **Images:** sent as an attachment with a publicly reachable HTTPS image URL.

## Turning off dry-run

Only after credentials are configured and you have tested with the review queue:

```env
DRY_RUN=false
```

The app refuses to start with `DRY_RUN=false` if Instagram credentials are missing.
