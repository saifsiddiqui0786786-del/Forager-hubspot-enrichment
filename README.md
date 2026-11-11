# Forager x HubSpot Enrichment

A Python service that enriches HubSpot companies and contacts with data from
[Forager](https://forager.ai). It runs live on Railway and is triggered by
HubSpot webhooks.

**Live URL:** https://enrichment-api-production-1baedf7f.up.railway.app

## What it does

| # | Task | How |
|---|---|---|
| 1 | Enrich companies | Description, revenue, headcount, founded year, industry, LinkedIn page, address, keywords |
| 2 | Enrich contacts | Name, job title, LinkedIn URL, employer name / domain / LinkedIn, email, phone, city, state, country |
| 3 | Find contacts at a company | Searches current employees, filtered by job title |
| 4 | Company webhook | New company in HubSpot -> enrich it -> enrich its contacts if it has any, otherwise find new ones |
| 5 | Contact webhook | New contact in HubSpot -> enrich it |

## How it works

```
HubSpot (company or contact created)
        |  webhook, signed by HubSpot
        v
POST /webhooks/hubspot      1. verify the signature
        |                   2. reply 200 straight away
        v                   3. do the work in the background
Enrichment service  --->  Forager API  (company, people, email, phone)
        |
        v
HubSpot CRM API  (update the company, create / update contacts)
```

| File | Role |
|---|---|
| `app/main.py` | Web server: webhook endpoint, signature check, manual endpoints |
| `app/enrichment.py` | The workflows: enrich company, enrich contact, find contacts |
| `app/forager.py` | Forager API client |
| `app/hubspot.py` | HubSpot CRM API client |
| `app/mapping.py` | Turns Forager records into HubSpot properties (pure functions, unit tested) |
| `app/config.py` | Settings, read from environment variables |
| `app/setup_hubspot.py` | One-time creation of the custom HubSpot properties |
| `hubspot-app/` | HubSpot app definition: scopes and the two webhook subscriptions |

## Job title filtering

Who gets surfaced at a company is controlled in three places, most specific first:

1. **Per request** - `POST /find-contacts` with `"job_titles": ["Head of Sales", "VP Sales"]`.
2. **Per company** - set the company property **Job Title Filter** in HubSpot
   (comma separated). The company webhook reads it.
3. **Default** - the `DEFAULT_JOB_TITLES` environment variable.

Forager's title search is fuzzy, so every result is checked again in code and
only whole-word matches are kept ("VP" matches "VP Finance", not "MVP Engineer").

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Service status |
| POST | `/webhooks/hubspot` | Receives HubSpot creation events (signature verified) |
| POST | `/enrich/company/{id}` | Enrich one company |
| POST | `/enrich/contact/{id}` | Enrich one contact |
| POST | `/find-contacts` | Find people at a company by job title |

The three manual endpoints need the header `X-API-Key: <SERVICE_API_KEY>`.

```bash
curl -X POST https://<service-url>/find-contacts \
  -H "X-API-Key: $SERVICE_API_KEY" -H "Content-Type: application/json" \
  -d '{"company_id": "123456", "job_titles": ["Head of", "VP"], "limit": 5}'
```

## Design decisions

- **Secrets live in environment variables.** Nothing is hard-coded; `.env` is git-ignored.
- **Webhooks are verified** with HubSpot's v3 signature (HMAC-SHA256 with the app's client secret), and requests older than 5 minutes are rejected.
- **Fast reply, background work.** HubSpot expects an answer within 5 seconds, so the endpoint replies at once and enriches afterwards.
- **No loops.** Contacts created by this service are stamped with `forager_enriched_at`, so the contact webhook they trigger is skipped. Duplicate webhook deliveries are ignored.
- **Never overwrites with blanks.** Empty Forager values are dropped before writing to HubSpot.
- **Credit aware.** The free autocomplete lookup picks the right company before a paid call. When finding contacts the checks run cheapest first (location, then email, then phone), and Forager only charges for an email or phone it actually finds.
- **Right company, not just right domain.** Dozens of Forager records share `openai.com` ("ChatGPT", "DALL-E", look-alikes). Candidates are scored on domain and name so the real company wins.
- **Complete records only.** With `ONLY_COMPLETE_CONTACTS=true`, a found person is saved only when they have an email, a phone and a full city / state / country.
- **Retries.** Rate limits and temporary server errors are retried with backoff.

## Run locally

```bash
python -m venv .venv
.venv/Scripts/activate            # macOS / Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env              # then fill in the values
python -m app.setup_hubspot       # one time: creates the custom properties
uvicorn app.main:app --reload
python -m pytest                  # unit tests
```

## Deploy

**Railway**

```bash
railway init
railway up
railway variables --set "FORAGER_API_KEY=..." --set "FORAGER_ACCOUNT_ID=..." \
  --set "HUBSPOT_ACCESS_TOKEN=..." --set "HUBSPOT_CLIENT_SECRET=..." --set "SERVICE_API_KEY=..."
```

**HubSpot app** (webhooks need a project-based app, built with the HubSpot CLI)

```bash
cd hubspot-app
hs project upload        # creates the app with its scopes and webhook subscriptions
hs project install-app   # installs it in the account
```

The webhook target URL is set in `hubspot-app/src/app/webhooks/webhooks-hsmeta.json`.
The access token and client secret are on the app's Auth tab in HubSpot.

## Environment variables

| Variable | Required | Purpose |
|---|---|---|
| `FORAGER_API_KEY` | yes | Forager API key |
| `FORAGER_ACCOUNT_ID` | yes | Forager account id (part of every API URL) |
| `HUBSPOT_ACCESS_TOKEN` | yes | HubSpot app access token |
| `HUBSPOT_CLIENT_SECRET` | yes | Verifies webhook signatures |
| `SERVICE_API_KEY` | yes | Protects the manual endpoints |
| `DEFAULT_JOB_TITLES` | no | Default title filter, comma separated |
| `DEFAULT_CONTACT_LIMIT` | no | Contacts to find per company (default 5) |
| `MAX_CANDIDATES` | no | Most people examined per search (default 25) |
| `ONLY_COMPLETE_CONTACTS` | no | Save only complete records (default true) |
| `FORAGER_PHONE_QUALITY_TIER` | no | `most_coverage` or `highest_quality` |

## Forager credit costs (trial plan, measured)

| Call | Credits |
|---|---|
| Company autocomplete | 0 |
| Company record | 1 |
| People search (10 results) | 1 |
| Work email | 5, only when found |
| Phone number | 9, only when found |

A complete contact costs about 14 credits.

See [NOTES.md](NOTES.md) for extra Forager data worth discussing.
