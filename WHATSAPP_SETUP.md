# AY Legacy Hostel — WhatsApp Business Platform setup

**Sender number:** 0537386377 (international format: 233537386377). Currently on the WhatsApp Business phone app.

## Preserve the existing WhatsApp Business app
Check eligibility for **WhatsApp Business App Coexistence** using an eligible Meta/BSP Embedded Signup flow. Do not deregister, delete or migrate the existing number through standard Cloud API registration before confirming coexistence. Keep a backup of existing chats.

## Meta setup
1. Use Meta Business Suite to select/create the AY Legacy Hostel business portfolio.
2. Complete Meta business verification if required, then onboard the existing number using a coexistence-compatible flow.
3. Confirm WhatsApp Business Account (WABA) ID and **Phone Number ID**; these are not the same as the public phone number.
4. Create an approved **no-variable** utility message template, for example `hostel_inspection_notice` (language `en_US`). Template wording must match its approved purpose.
5. Configure Meta billing, consent and opt-out process. API messages can incur per-message charges.
6. In Render, configure these **secret environment variables**:
   - `WA_ACCESS_TOKEN` — production system-user token with WhatsApp messaging access.
   - `WA_PHONE_NUMBER_ID` — Meta phone number ID.
   - `WA_GRAPH_VERSION` — current supported Graph API version, e.g. `v25.0` if supported for your account.
   - `WA_VERIFY_TOKEN` — random webhook verification string.
   - `WA_APP_SECRET` — Meta App Secret used to verify webhook signatures.
7. Configure Meta webhook callback URL `https://YOUR-RENDER-HOST/whatsapp/webhook`, verify using `WA_VERIFY_TOKEN`, subscribe to message status events.
8. Test one opted-in student and verify accepted, delivered and failed outcomes before bulk use.

**Never paste access tokens, app secrets, or verification codes into GitHub, screenshots, or this chat.**

## Access controls
Admin always has `whatsapp.send`. Staff accounts do not receive it by default. Admin can grant/revoke it via Staff > Permissions. Staff messaging permission does not grant financial-report access.

## Consent
The Admin must record an explicit WhatsApp opt-in for each active student. Student registration alone is not opt-in. Revoking consent immediately excludes that student from new campaigns.

## Usage
Open **WhatsApp** in the app. Select all active opted-in students or one student; enter the exact Meta-approved no-variable template name and language. Type SEND to confirm. One click sends an individual API request to each eligible student, capped at 250 per campaign. Status "accepted" is not proof of delivery; the webhook updates statuses.

## Production limitations and pre-launch checks
- The first release uses synchronous sends in the web request. For reliable bulk delivery in production, replace this with a durable background queue, retries, per-recipient idempotency, and concurrency control before allowing large campaigns.
- The current form supports only **approved templates without variables**; personalized messages need template parameter support.
- Confirm compatibility with Render request timeouts and current PostgreSQL schema.
- Audit and test the app's permissions, consent flows, CSRF, Meta signature verification, status webhook, and rate limits.
- Disable bulk sends until Meta onboarding, billing, testing and operational review are complete.
