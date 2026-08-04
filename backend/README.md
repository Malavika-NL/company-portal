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

1. Create PostgreSQL database `portal_identity` in the local PostgreSQL
   server used by pgAdmin.
2. Copy `.env.example` to `.env` and supply the PostgreSQL password.
3. Set the Marketing client URLs/secret in `.env`, load the environment
   variables, then run `python manage.py migrate`.
4. Run `python manage.py runserver 8004`.

The initial migration seeds `nl-technologies` and `vbs`. Migration 0003 removes
legacy non-superuser portal passwords and copied email identifiers; link users
through `PortalProfile.marketing_user_id` in the admin instead.

## Local portal startup

## Docker startup

From the repository root, run:

```powershell
docker compose up --build -d
```

The portal will be available at `http://192.168.1.56:8002`. This Compose file
starts the portal frontend, portal backend, and PostgreSQL only. Configure the
Marketing CRM, SalesPie, and BDCRM URLs and shared SSO secret as environment
variables on the `backend` service when those separately maintained services
are deployed.

For normal local development, start the portal from the `frontend` directory:

```powershell
npm.cmd run dev
```

This starts a local frontend at `http://localhost:8012` and uses the portal
backend at port 8004. It deliberately does not probe or start CRM login
endpoints in the background. First start the backend in Docker so it can reach
the CRM APIs through their Docker networks:

```powershell
$env:PORTAL_FRONTEND_URL = 'http://localhost:8012'
docker compose up --build backend
```

Then, in a second terminal:

```powershell
cd frontend
npm.cmd run dev:local
```

To choose a startup mode, run one of these commands from `frontend`:

| Command | Starts |
| --- | --- |
| `npm.cmd run dev`, `dev:all`, `dev:portal`, or `dev:local` | Local frontend on port 8012 alongside the Docker backend |
| `npm.cmd run dev:standalone` | Standalone portal on port 8002; use only when all CRM services also run directly on the host |
| `npm run dev:marketing` | Marketing CRM only |
| `npm run dev:salespie` | SalesPie only |
| `npm run dev:bdcrm` | BDCRM only |
