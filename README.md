# Sales Command Centre

Phase 1 prototype of a local sales workspace. It runs on this machine, uses fictional demo data, and does not call Outlook, Gong, HubSpot, Excel, Calendly, or any AI service.

## Requirements

- Python 3.11 or newer
- Node.js 20 or newer (used once, to build the dashboard)

## Run

From the repository root:

```bash
python3 main.py
```

If `python` on your PATH is Python 3.11 or newer, `python main.py` does the same thing.

The first launch:

1. Checks the Python version and configuration.
2. Creates `.venv` and installs `requirements.txt`. If that Python has no `ensurepip` module, pip is installed into the environment first.
3. Installs the dashboard dependencies and builds the React app when the build is missing or stale.
4. Creates `data/command.db`, applies the schema, and loads the fictional Helios Industrial portfolio.
5. Serves the API and the dashboard on [http://127.0.0.1:8741/](http://127.0.0.1:8741/).
6. Opens that address in the default browser.

Press Ctrl+C to stop. The process logs the shutdown.

Useful flags:

```bash
python3 main.py --no-browser
python3 main.py --port 8742
python3 main.py --database data/other.db
python3 main.py --log-level DEBUG
```

The server only binds to `127.0.0.1`, `localhost`, or `::1`. A public host is refused.

Optional overrides live in `config/local.yaml` (gitignored). Copy values from `config/default.yaml`. Environment variables `WATSON_HOST`, `WATSON_PORT`, `WATSON_DATABASE`, `WATSON_LOG_LEVEL`, and `WATSON_NO_BROWSER=1` override the file.

## What you can do

The sidebar covers Overview, Daily Briefing, Deals, Customers, Commitments, Approvals, Forecast, Files & Reports, Activity, Integrations, and Settings.

Filters for date, owner, pipeline, region, business unit, product, customer, opportunity, and stage are shared across the portfolio views. Open a customer or a deal for the people, commitments, and timeline. The Approvals page can approve, reject, edit, or defer. Those decisions are stored in the local database and do not leave the machine. Integrations can be refreshed; the refresh writes a local log and fetches nothing.

Everything on screen is labelled demo data. Forecast adjustments and other “AI” lines are simulated placeholders. No model is called.

## Tests

After the first launch, the project virtual environment can run the tests:

```bash
.venv/bin/python -m pytest
```

Or, without using that environment:

```bash
pip install -r requirements.txt
python3 -m pytest
```

The dashboard build is `npm run build` inside `frontend/`.

## What this phase does not do

- No live integrations and no outbound email.
- No Ollama and no external AI API.
- No encryption. The SQLite file holds fictional data only.
- No sign-in. Actions are recorded as Alex Harrington, the demo user.

Phase 2, not started here, is the local AI foundation: a local model, an AI service abstraction, structured outputs, and processing limits. It stays off external AI APIs.
