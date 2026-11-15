# Account setup, from zero

Everything here is done in a browser by you. Nothing is committed and no secret
enters the chat. Times are realistic, including waiting for verification.

**Order matters.** Meta needs a Facebook Page before it will expose a Page token;
Instagram Reels need a professional account; R2 comes last because nothing else
depends on it.

| # | Account | Why | Time |
|---|---|---|---|
| 1 | Google account | owns the YouTube channel | 0–5 min (probably exists) |
| 2 | YouTube channel | the Shorts destination | 5 min |
| 3 | Facebook personal account | required to create a Page and a Meta app | 0–10 min |
| 4 | Facebook Page | the Facebook Reels destination | 5 min |
| 5 | Instagram professional account | the Instagram Reels destination | 5 min |
| 6 | Meta developer app | issues the tokens | 15 min |
| 7 | Cloudflare account + R2 bucket | public URL Instagram can fetch | 10 min |

Total: **~50 minutes**, plus any identity-verification wait.

---

## 1. Google account

If you already have a Google account you can use it — but consider a **dedicated
one** for the channel (`yourchannel@gmail.com`). It keeps the channel's Cloud
project, OAuth client and quota separable from your personal life, and it costs
nothing.

If you create one: <https://accounts.google.com/signup>. Turn on 2FA.

> **Do not** mix a personal Gmail you rely on with a channel you may later sell or
> hand to an editor. A Brand Account (step 2) can be transferred; a personal
> account cannot.

---

## 2. YouTube channel

1. Sign in, go to <https://youtube.com> → click your avatar → **Create a channel**.
2. Choose a **channel name** — this is the public brand, changeable later.
3. Accept, then finish the "make it yours" prompts (handle, picture).

Get the handle you want now (<https://youtube.com/handle>). Handles are permanent
-ish and first-come.

> If you want the channel detached from your personal Google identity, create a
> **Brand Account**: <https://myaccount.google.com/brandaccounts>. You then sign
> into the channel *through* your Google account, and the channel's data is
> separate.

Nothing else to do here yet — the API side is step 6's Google Cloud work, done
later in the same account.

---

## 3. Facebook personal account

Meta **requires** a real personal Facebook account to create a Page and a
developer app. There is no way around it, and a throwaway account is the fastest
way to get everything permanently banned.

- Use your real account, at least a year old.
- Turn on 2FA: Settings → Password and security.

If you have no Facebook account and do not want one: you can still do **YouTube
only** and skip sections 3–6. See "If you skip Meta" at the end.

---

## 4. Facebook Page

1. <https://www.facebook.com/pages/create> → name it after the channel.
2. Category: something honest for the content (e.g. *Education*, *Personal blog*).
3. You will be the sole admin. That's what grants publish rights.

Nothing needs posting to the Page. It exists to own the Reels.

---

## 5. Instagram professional account

1. In the Instagram app: **Settings → Account type and tools → Switch to
   professional account** → choose **Creator** (or Business). Personal accounts
   cannot publish through the API.
2. If you're using the Facebook-login path (recommended, covers both platforms):
   **Settings → Business tools and controls → Connect a Facebook Page** → pick the
   Page from step 4.
3. Pick a handle matching the channel.

> **This is the single most common failure point.** If this link is missing,
> `meta_setup.py` prints `ig_business_id=-` and Instagram publishing cannot work.

---

## 6. Meta developer app

1. <https://developers.facebook.com> → **My Apps → Create app**.
2. App type: **Business**. Name it after the channel.
3. In the app dashboard, add the **Instagram** product.
4. **App settings → Basic** → copy **App ID** and **App secret**:

   ```bash
   cd ~/Documents/reelforge
   .venv/bin/python scripts/set_env.py meta     # enters META_APP_ID, META_APP_SECRET
   ```

5. **App roles → Roles** — your account must be listed as **Administrator**
   (true by default as creator). You do **not** need App Review, because you are
   only publishing to an account that holds a role on your own app.

6. Generate a user token: <https://developers.facebook.com/tools/explorer>
   - top right: select your app
   - **Permissions**, add:
     `pages_show_list`, `pages_read_engagement`, `pages_manage_posts`,
     `publish_video`, `instagram_basic`, `instagram_content_publish`
   - click **Generate Access Token**, approve the popup, copy the token
   - feed it in:

   ```bash
   .venv/bin/python scripts/set_env.py set IG_USER_TOKEN
   ```

7. Derive the rest (this is the step that saves you an hour of token archaeology):

   ```bash
   .venv/bin/python scripts/meta_setup.py "$(grep '^IG_USER_TOKEN=' .env | cut -d= -f2-)"
   ```

   It prints `FB_PAGE_ID`, `FB_PAGE_TOKEN`, `IG_USER_ID`, `IG_ACCESS_TOKEN`.
   Add those with `scripts/set_env.py set <KEY>`.

> The Graph API Explorer token lasts ~1 hour. `meta_setup.py` exchanges it for a
> 60-day user token *and* a non-expiring Page token — which is why `META_APP_ID`
> and `META_APP_SECRET` must be in `.env` **before** you run it.

**Keep the app in Development mode.** That is correct and sufficient for
publishing to your own accounts, and it avoids the review queue entirely.

---

## 7. Cloudflare account + R2

Only needed for Instagram (it fetches the file from a public URL). Skip if you go
YouTube + Facebook only.

1. <https://dash.cloudflare.com/sign-up> → verify email.
2. **R2 Object Storage** → **Create bucket** → name it e.g. `reelforge-reels`.
3. Bucket → **Settings → Public access → R2.dev subdomain → Allow**.
   Copy the `https://pub-xxxxxxxx.r2.dev` URL — that's `R2_PUBLIC_BASE`.
4. **R2 → Manage R2 API Tokens → Create API token** → permission
   **Object Read & Write**, scoped to that bucket.
   Copy the **Access Key ID** and **Secret Access Key** (shown once).
5. Your **Account ID** is on the R2 overview page (right-hand sidebar).

```bash
.venv/bin/python scripts/set_env.py r2
.venv/bin/python scripts/set_env.py set IG_HOST      # type: r2
```

Free tier: **10 GB storage, 1M Class A ops, 10M Class B ops, egress free**. A
35 s reel is ~18 MB, so this holds ~500 reels before it costs anything, and each
post is a handful of operations.

> The `r2.dev` URL is rate-limited and Cloudflare calls it non-production. For a
> few posts a day it is fine. If you later want a stable, unbranded URL, attach a
> custom domain you have on Cloudflare (R2 → Settings → Custom domain).

---

## 8. Google Cloud: the YouTube API (do this last)

1. <https://console.cloud.google.com> → project picker → **New project**
   (`reelforge`).
2. **APIs & Services → Library** → search **YouTube Data API v3** → **Enable**.
3. **APIs & Services → OAuth consent screen**
   - User type **External** → fill app name + your email
   - **Publish app** (button on the consent screen page)
4. **Credentials → Create credentials → OAuth client ID**
   - Application type: **Desktop app**
   - **Download JSON**
5. Install it:

   ```bash
   mkdir -p ~/Documents/reelforge/secrets
   mv ~/Downloads/client_secret_*.json \
      ~/Documents/reelforge/secrets/yt_client_secret.json
   ```

Two things that will otherwise bite you:

- **Staying in "Testing" expires your refresh token every 7 days.** The consent
  screen must say **In production**. Unverified is fine for your own channel.
- An **unaudited** project has its API uploads forced to **private**. Request the
  audit form once if you need public uploads. `reelforge` warns you if the upload
  came back private.

Quota: one upload costs 1,600 of the 10,000 daily units → **~6 uploads/day**.

---

## 9. Verify

```bash
cd ~/Documents/reelforge
.venv/bin/python scripts/set_env.py show   # masked values, safe to screenshot
./reelforge doctor                         # pass/fail per requirement
```

Everything under `publish` should read **OK**.

---

## 10. First publish — one platform at a time

```bash
./reelforge publish the-five-minute-rule-that-ends-procrastination \
  --platforms youtube --yes
```

The first run opens a browser; sign in with the account from step 1 and click
**Allow**. The token is cached in `secrets/yt_token.json` and never asked again.

Then add the others:

```bash
./reelforge publish the-five-minute-rule-that-ends-procrastination \
  --platforms instagram,facebook --yes
```

Re-running is safe: published platforms are recorded and skipped.

---

## If you skip Meta

You keep YouTube Shorts only. Set `IG_LOGIN_TYPE` aside, disable the other two:

```yaml
# config/channel.yaml
publish:
  instagram: { enabled: false }
  facebook:  { enabled: false }
```

---

## Traps, collected

| Trap | Symptom | Fix |
|---|---|---|
| OAuth app left in **Testing** | uploads stop after ~7 days | publish the consent screen |
| Unaudited Google project | upload private, not public | request the YouTube API audit |
| IG left as a **personal** account | `instagram_content_publish` refused | switch to Creator/Business |
| IG not linked to a **Page** | `ig_business_id=-` | Instagram → Business tools → connect Page |
| Meta app in **Live** without review | permissions silently missing | Development mode is correct for your own accounts |
| Graph Explorer token expires | works for an hour, then 401 | run `meta_setup.py` with app id + secret |
| R2 bucket not public | Instagram returns `Media download failed` | enable the r2.dev Public Development URL |
| Throwaway Facebook account | everything gets banned | use a real personal account |
