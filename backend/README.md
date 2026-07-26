# Company Portal backend

This is a separate identity and SSO service. Marketing CRM is the password
authority: the portal never accepts, stores, copies, or verifies normal-user
passwords, hashes, Marketing emails, or Marketing user records.

The portal stores a `PortalProfile.marketing_user_id` (the immutable Marketing
local user ID), company memberships, and explicit SalesPie/BDCRM local user-ID
mappings. It never matches CRM users by email.

## Marketing CRM provider contract

1. `POST /api/auth/marketing/start/` with `company_code` returns an
   `authorization_url`. Redirect the browser there after NL/VBS selection.
2. Marketing CRM hosts the configured authorize endpoint, validates its own
   local email/password, and redirects to `MARKETING_CRM_REDIRECT_URI` with a
   short-lived, one-time `code` and the unchanged `state`.
3. The portal callback redeems the code server-to-server at
   `MARKETING_CRM_TOKEN_URL`. Marketing must authenticate the portal client and
   return JSON exactly like `{"marketing_user_id": "15"}`. Do not return an
   email, password, or password hash.
4. The portal finds `PortalProfile.marketing_user_id`, verifies the selected
   company membership, and returns the portal JWT session. Existing SSO launch
   endpoints then send each CRM a short-lived one-time code, which it exchanges
   for the explicitly mapped local user ID.

1. Create PostgreSQL database `portal_identity`.
2. Copy `.env.example` to `.env` and supply the PostgreSQL password.
3. Set the Marketing client URLs/secret in `.env`, load the environment
   variables, then run `python manage.py migrate`.
4. Run `python manage.py runserver 8004`.

The initial migration seeds `nl-technologies` and `vbs`. Migration 0003 removes
legacy non-superuser portal passwords and copied email identifiers; link users
through `PortalProfile.marketing_user_id` in the admin instead.

## Local portal startup

For normal local development, start the portal from the `frontend` directory:

```powershell
npm run dev
```

This starts the portal at `http://127.0.0.1:5176`, starts the portal backend on
port 8004, and automatically warms the Marketing CRM, SalesPie, and BDCRM
backends and frontends. Running the command again reuses the existing portal
instead of attempting to start a second server on port 5176.

To choose a startup mode, run one of these commands from `frontend`:

| Command | Starts |
| --- | --- |
| `npm run dev` or `npm run dev:all` | Company Portal and all three CRMs |
| `npm run dev:portal` | Company Portal only, without CRM warm-up |
| `npm run dev:marketing` | Marketing CRM only |
| `npm run dev:salespie` | SalesPie only |
| `npm run dev:bdcrm` | BDCRM only |
