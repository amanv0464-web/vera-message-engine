# Vera Message Engine

A deterministic, stateful FastAPI bot for the magicpin Vera AI Challenge. It uses the category, merchant, trigger, and optional customer context supplied by the judge to choose one grounded next action. It does not call an LLM and therefore never needs an API key in production.

## What it does

- Implements `GET /v1/healthz`, `GET /v1/metadata`, `POST /v1/context`, `POST /v1/tick`, and `POST /v1/reply`.
- Stores versioned context atomically in memory and accepts only newer versions.
- Selects trigger-specific actions for research, performance shifts, listing fixes, seasonal moments, competitors, safety alerts, planning intent, recalls, refills, trials, and lapsed customers.
- Uses active merchant offers only; expired and category-catalog offers are never presented as merchant offers.
- Uses customer outreach only when consent/context is present, and suppresses duplicate sends by the supplied suppression key.
- Ends safely on opt-out, hostile, and automated replies; moves directly to a concrete next step after a commitment.
- Implements optional `POST /v1/teardown` to erase synthetic test state.

## Run locally

Use Python 3.11+.

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8080
```

In a second terminal, generate the shared data set and run the official harness:

```bash
python dataset/generate_dataset.py --seed-dir dataset --out expanded
# Set LLM_PROVIDER and LLM_API_KEY in judge_simulator.py first.
python judge_simulator.py
```

The official judge simulator is LLM-powered, so its scoring step needs an API key for whichever provider is selected there. The bot itself uses no secret and makes no outbound calls.

### OpenRouter judge configuration

The included simulator is configured to read OpenRouter settings from environment variables; the key is never saved in the repository. In PowerShell, set them only in the terminal session where you run the score:

```powershell
$env:LLM_PROVIDER = "openrouter"
$env:OPENROUTER_API_KEY = "your-newly-rotated-key"
$env:LLM_MODEL = "nvidia/nemotron-3-ultra-550b-a55b:free"
python judge_simulator.py
```

Use a newly created key, not one previously pasted into chat or committed to a file.

## Configure submission metadata

Copy `.env.example` values into your hosting environment:

- `VERA_TEAM_NAME`
- `VERA_TEAM_MEMBERS` (comma-separated)
- `VERA_CONTACT_EMAIL`

## Deploy

Deploy this directory to a service that can expose a public HTTPS URL, then use its base URL in the challenge portal. A suitable start command is:

```bash
uvicorn app:app --host 0.0.0.0 --port $PORT
```

The included `Dockerfile` works with hosts that accept a container deployment. It reads the platform-provided `PORT` and requires no application secret.

For local exposure while testing, point a tunnel at port `8080`. Do not commit real environment files or API keys.
