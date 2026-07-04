# Docker Secrets

Each file in this directory contains a single secret value (no quotes, no
trailing newline). Docker Compose mounts them into containers at
`/run/secrets/<filename>`.

This directory is gitignored. To set up a new deployment, create each file
below and paste in the raw key value.

## Required secrets

### openrouter_api_key
LLM API access for the 4-phase pipeline (Analyst, Formatter, SEO, Manager).
Get a key at https://openrouter.ai/settings/keys — starts with `sk-or-v1-`.

### airtable_api_key
Read-only access to the PBS Wisconsin Single Source of Truth table.
Create a Personal Access Token at https://airtable.com/create/tokens with
read-only scopes on the `appZ2HGwhiifQToB6` base. Starts with `pat`.

### langfuse_public_key
Observability — traces LLM calls, token usage, and costs.
Create an API key pair in your Langfuse project settings
(Settings > API Keys). Starts with `pk-lf-`.

### langfuse_secret_key
The secret half of the Langfuse API key pair (created at the same time
as the public key above). Starts with `sk-lf-`.

### local_llm_api_key
Only needed when routing a pipeline phase to the `local-llm` backend (oMLX on the
Mac Studio). This is the Bearer key oMLX enforces — it lives in 1Password
("oMLX Local LLM Key"). Omit the file entirely if no phase uses `local-llm`
(the backend is keyless-tolerant, but oMLX will reject unauthenticated calls).

## Optional secrets (passed as env vars, not files)

### CARDIGAN_API_KEY
API authentication for the Cardigan dashboard. Set as an environment
variable in `.env` or your shell. When empty or absent, auth is disabled
(dev mode). Generate with: `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`

### CLOUDFLARE_TUNNEL_TOKEN
Only needed when running with `--profile tunnel` for remote access.
Set as an environment variable. Get from Cloudflare Zero Trust >
Tunnels > configure your tunnel. Starts with `eyJ`.
