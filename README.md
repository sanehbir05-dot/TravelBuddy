# Travel Buddy

TIET Patiala students post upcoming trips, find classmates going the same way at a similar time, and choose to travel together. Sign-up needs an `@thapar.edu` email. Travel Buddy only helps students find each other. It doesn't sell tickets or arrange transport.

It works in any browser on a laptop or phone, and it can be installed to the phone home screen as an app (PWA).

## Run it on your computer

```bash
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

flask --app app db upgrade         # creates the database tables (SQLite file in instance/)
flask --app app seed               # optional: 4 demo students with trips
flask --app app run                # http://127.0.0.1:5000
```

Demo logins (after `seed`): `priya@thapar.edu`, `ananya@thapar.edu`, `rahul@thapar.edu`, `karan@thapar.edu`, all with the password `password123`.

If email isn't configured, verification and password-reset codes appear on screen, marked "Dev mode".

**Tests:** run `python -m unittest test_travel_buddy -v`. To run the same tests on Postgres, set `TEST_DATABASE_URL=postgresql://...` first. GitHub Actions runs them on SQLite and Postgres for every push.

## Put it online

The plan is Render for the web server, Neon for the Postgres database, and Brevo for email. Each has a free tier. Render's always-on plan costs about $7 a month.

1. **Database (Neon).** Create a project at neon.tech in the Singapore region and copy the connection string (`postgresql://...`).
2. **Email (Brevo).** Create an account at brevo.com and add a sender. Your own domain is best, because mail from a real domain is less likely to be filtered by Thapar's email system. Under **SMTP & API**, copy the SMTP login and create an SMTP key. Resend works too (see `.env.example`).
3. **Web server (Render).** Choose **New → Blueprint** and pick this repository. `render.yaml` sets everything up. Render then asks for:
   - `DATABASE_URL`: the Neon connection string
   - `SITE_URL`: the address Render gives you, for example `https://travel-buddy.onrender.com`
   - `ADMIN_EMAILS`: your @thapar.edu address, which is allowed to review reports at `/admin/reports`
   - `MAIL_USERNAME`, `MAIL_PASSWORD`, `MAIL_FROM`: from Brevo

   `SECRET_KEY` is generated automatically, and the database tables are created on every deploy with `flask db upgrade`.
4. **Check it.** Sign up with a real @thapar.edu address and confirm the code email arrives and isn't in spam. Install it on a phone: on Android use Chrome's menu and choose "Install app". On iPhone use Safari's Share button and choose "Add to Home Screen".

## What it does

| Area | Behaviour |
|---|---|
| Sign-up | Email and password, `@thapar.edu` only. A 6-digit code is sent to the email (expires in 10 minutes, 5 tries). |
| Password reset | "Forgot your password?" emails a code. The response looks the same whether or not the account exists. |
| Limits | Login is limited to 10 tries a minute and 50 an hour per IP. Sign-up, code checks and resends are also limited. |
| Trips | From, to, date, approximate time, transport and preferences. Dates use India time. |
| Matching | Same college, same destination ("Delhi", "New Delhi" and "Delhi ISBT" all match), leaving within ±3 hours. The window can be changed and there's a batch filter. |
| Women-only | Female students can make a trip women-only. It is then hidden from everyone else and only matches women. |
| Requests and chat | Requests are accepted or declined. Chat opens after acceptance and checks for new messages every 8 seconds. Emails go out when a request is sent or accepted, and can be turned off in Profile. |
| Privacy | Before acceptance, others see only first name, last initial and batch. Contact handles are shared only by choice. The privacy page is at `/privacy`. |
| Safety | Block, report, auto-hide after 3 reports, and an admin review page. |
| Account deletion | Profile → Delete account. Trips and requests are cancelled and personal details are wiped. |
| Installable app | Manifest, icons and a service worker with an offline page. |

## Configuration

See [`.env.example`](.env.example). Set `APP_ENV=production` online, which turns on secure cookies, HTTPS-aware proxying and a required `SECRET_KEY`. Rate limits are stored in memory per server process. That's fine for one Render instance. If you scale up, set `RATELIMIT_STORAGE_URI` to a Redis URL.

## Changing the database

```bash
# after editing a model in app.py
flask --app app db migrate -m "describe the change"
flask --app app db upgrade
```

Commit the new file in `migrations/versions/`. Render applies it on the next deploy.

## Structure

```
app.py                  models, matching, routes, emails, `flask seed`
migrations/             database schema history (Alembic)
templates/              pages (Jinja + Bootstrap 5)
static/                 PWA manifest, service worker, app icons
render.yaml             Render deployment
test_travel_buddy.py    end-to-end tests
docs/                   site audit and screenshots
```

## Still to decide before launch

- Fill in the placeholders on `/privacy`: the contact email, the date, and whether TIET is involved.
- Pick a sender domain for emails.
