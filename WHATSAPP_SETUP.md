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
7. Create a **separate Render background worker service** using the same repository, branch/deployment, PostgreSQL DATABASE_URL, and WA_* secrets. Set its start command to `python whatsapp_worker.py`. Do not run multiple SQLite workers. Ensure both services use the same database. Once the worker is running, set `WA_WORKER_ENABLED=1` on the **web service**; leave it unset until then.\n8. Configure Meta webhook callback URL `https://YOUR-RENDER-HOST/whatsapp/webhook`, verify using `WA_VERIFY_TOKEN`, subscribe to message status events.
9. Test one opted-in student and verify accepted, delivered and failed outcomes before bulk use.

**Never paste access tokens, app secrets, or verification codes into GitHub, screenshots, or this chat.**

## Access controls
Admin always has `whatsapp.send`. Staff accounts do not receive it by default. Admin can grant/revoke it via Staff > Permissions. Staff messaging permission does not grant financial-report access.

## Consent
The Admin must record an explicit WhatsApp opt-in for each active student. Student registration alone is not opt-in. Revoking consent immediately excludes that student from new campaigns.

## Usage
Open **WhatsApp** in the app. Select all active opted-in students or one student; enter the exact Meta-approved no-variable template name and language. Type SEND to confirm. One click queues a delivery for each eligible student (up to 250); the separately deployed worker sends them. Without the worker and WA_WORKER_ENABLED=1, sending stays disabled. Status "accepted" is not proof of delivery; the webhook updates statuses.

## Production limitations and pre-launch checks
- This version stores queued deliveries in the database and uses a separate worker. PostgreSQL workers use row locking; configure a single worker initially. Delivery rows left in 'processing' after a worker crash require manual investigation; do not automatically retry unknown outcomes, which might duplicate messages.\n- Worker deployment and queue operations have not been tested on Render. Confirm the worker service is actually running before enabling sends.\n- Meta webhook notifications must be tested for correct signature verification and delivery updates.
- The current form supports only **approved templates without variables**; personalized messages need template parameter support.
- Confirm PostgreSQL compatibility and review deployment logs before enabling any sends.
- Audit and test the app's permissions, consent flows, CSRF, Meta signature verification, status webhook, and rate limits.
- Disable bulk sends until Meta onboarding, billing, testing and operational review are complete.\n- Existing PR #5 legal page links must remain intact when merging changes to templates/base.html.\n- Do not enable WA_WORKER_ENABLED before successful onboarding and consent testing.
