# Penman AI — Supabase Auth email templates

Paste-ready HTML for every transactional email Supabase Auth can send for this
app. The templates use inline CSS and table layout so they render in Gmail and
other clients without external fonts, images or stylesheets.

## Templates

| Supabase template | File | Subject to paste |
|---|---|---|
| Reset password | `reset_password.html` | `Reset your Penman AI password` |
| Confirm signup | `confirm_signup.html` | `Confirm your Penman AI account` |
| Magic link | `magic_link.html` | `Your Penman AI sign-in link` |
| Invite user | `invite.html` | `You're invited to Penman AI` |
| Change email address | `email_change.html` | `Confirm your new Penman AI email` |
| Email changed | `email_changed.html` | `Your Penman AI email was changed` |

## Before deploying / testing

Run `supabase/migrations/20261126_stylometry_and_rls.sql` in the Supabase SQL
Editor on an existing project. It is idempotent and also repairs older tables
that are missing `updated_at` (without it, draft and profile updates fail with
`42703: record "new" has no field "updated_at"`). Fresh projects can use
`schema.sql` instead.

## How to install

1. Supabase Dashboard → **Authentication → Email Templates**.
2. Pick the template from the table above.
3. Paste the Subject value into **Subject**.
4. Open the **Source** toggle in the body editor, paste the entire contents of
   the matching `.html` file, then **Save changes**.
5. Repeat for all six templates.

These files are plain HTML for Supabase — they are **not** rendered by the
app's Jinja environment. The `{{ .TokenHash }}` / `{{ .SiteURL }}` /
`{{ .RedirectTo }}` placeholders are GoTrue variables and must be pasted
verbatim. Keep `&amp;` in the link URLs; HTML mail clients decode it to `&`.

## Where the links go

- **Reset password** uses `{{ .RedirectTo }}` because
  `POST /auth/forgot-password` sends `redirect_to=<APP_BASE_URL>/reset-password`.
- **All other action links** go to `GET /auth/confirm?token_hash=…&type=…`.
  That route calls `verify_otp`, sets HttpOnly session cookies, and redirects
  to `/drafts` for every link type.
- A bare `?code=` PKCE link is still handled by `auth.js` on the reset page if
  the browser holds the matching code verifier.

## Gmail SMTP checklist

- SMTP host `smtp.gmail.com`, port `587` (STARTTLS) or `465` (SSL).
- Username = full Gmail address; password = 16-character **App Password**
  (requires 2FA; the normal account password will not work).
- Supabase **Sender Email** must equal that Gmail address (or a verified
  "Send mail as" alias), or Gmail rejects the message as not owned by user.
- Add these to Supabase **Authentication → URL Configuration → Redirect URLs**:
  - `https://<your-render-host>/reset-password`
  - `https://<your-render-host>/auth/confirm`
- Set **Site URL** to `https://<your-render-host>` (it supplies
  `{{ .SiteURL }}` in all templates except reset password).
- Set `APP_BASE_URL=https://<your-render-host>` in `render.yaml` envVars (or
  the Render dashboard) so emailed links never trust the request Host header.
- Recovery/confirmation links are single-use and expire quickly (~1 hour).
  Gmail may flag them; ask the user to check Spam/Promotions.
