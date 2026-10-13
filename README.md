# Podcast Generation Agent

Generate source-grounded two-speaker podcasts from PDFs or text files with
FastAPI, LangGraph, DSPy, and Edge or Gemini text-to-speech.

## Setup

1. Create and activate a virtual environment.
2. Install the project with `python -m pip install -r requirements.txt`.
3. Copy `.env.example` to `.env` and configure default model IDs, optional model
   lists, and Telegram settings. Never commit `.env`.
4. Ensure FFmpeg is installed and available on `PATH`.

python -m podcast_generator.telegram.set_webhook
ngrok http 8000
Run the command-line generator with `python -m podcast_generator --help`.
Run the HTTP API with `uvicorn podcast_generator.api.main:app --host 0.0.0.0 --port 8000`.
Its interactive API documentation is available at `/docs`.

For each `POST /podcasts` request, provide your own `api_key` as multipart form
data. The service validates the key and holds it only for that request; it does
not use a server-side LLM key. For Gemini TTS, provide `gemini_api_key` as well,
unless the selected LLM provider is Gemini, in which case the same key is used.
`GET /providers` lists available providers, each provider's default model, and
the selectable model IDs. Select one by sending it as `model`, or enter a
custom model ID. Configure defaults with `GROQ_MODEL`, `OPENAI_MODEL`, and
`GEMINI_MODEL`; add comma-separated choices with `GROQ_MODELS`, `OPENAI_MODELS`,
and `GEMINI_MODELS`. Provider API keys do not belong in `.env`.

## Deploy to Render

Create a Blueprint in Render from this repository and select `render.yaml`.
It builds the included Dockerfile, which installs FFmpeg for audio generation.
The service listens on Render's `PORT`, runs a single worker for the in-memory
Telegram sessions, and uses `/health` for Render's health check.

The `/keep-alive` endpoint returns `{"status":"ok"}` and can be used by an
external uptime monitor. Render's free web services sleep after 15 minutes
without inbound traffic and can take about a minute to wake up. To reduce cold
starts, configure an external monitor to send a `GET` request to
`https://<your-service>.onrender.com/keep-alive` every 10-14 minutes. This
generates inbound traffic and uses free instance hours; use a paid always-on
instance if continuous availability is required.

For the Telegram bot, set `TELEGRAM_BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET`, and
`TELEGRAM_WEBHOOK_URL` in the Render service's environment. Set the webhook URL
to `https://<your-service>.onrender.com/telegram/webhook`, then register it
after deployment by putting those same values in your local `.env` file and
running `python -m podcast_generator.telegram.set_webhook`. Generated files and
in-memory jobs are temporary: Render's filesystem is ephemeral, and restarts
or spin-downs clear local files and active Telegram sessions.

## Telegram webhook

Set these values in `.env`:

- `TELEGRAM_BOT_TOKEN`: token from BotFather.
- `TELEGRAM_WEBHOOK_SECRET`: random 1-256 character value using letters,
  numbers, `_`, or `-`.
- `TELEGRAM_WEBHOOK_URL`: public HTTPS URL ending in `/telegram/webhook`.
- `TELEGRAM_MAX_FILE_SIZE`: maximum downloaded upload size in bytes (default
  `20971520`, 20 MiB).
- `TELEGRAM_MAX_TEXT_CHARACTERS`: maximum extracted/input text length
  (default `200000`).
- `TELEGRAM_MAX_AUDIO_SIZE`: maximum audio delivery size in bytes (default
  `52428800`, 50 MiB).
- `TELEGRAM_SESSION_TTL_SECONDS`: idle conversation expiry (default `1800`).
- `TELEGRAM_JOB_TIMEOUT_SECONDS`: maximum time budget for script and audio
  generation (default `1800`, 30 minutes).

Start the API, then register or update the webhook:

```text
python -m podcast_generator.telegram.set_webhook
```

Webhook registration also installs the Telegram bot menu commands:
`/devicesettings`, `/pdf2podcast`, `/script2podcast`, `/keys`, `/usage`, and
`/help`. Run the registration command again after updating the deployment to
refresh the menu. `/start` and `/cancel` remain available as additional
commands.

Transient Telegram HTTPS connection failures are retried up to five times with
exponential backoff for Bot API calls and file downloads.

In a private chat, send `/start` and upload a PDF or TXT file. Quick start uses
the configured Groq model for script generation and Gemini TTS with the
configured `GEMINI_TTS_MODEL`. After the file is checked, open the settings
Web App to use or edit device-local provider keys, model, TTS provider/model,
and podcast preferences. `/settings` opens the same form at any time.

For PDF podcasts, `/pdf2podcast` (or PDF from `/start`) first lets the user
choose a general source-grounded podcast, exam preparation, research-paper
explanation, job-interview preparation, concept learning, or a custom
instruction of up to 2,000 characters. The selected guidance is combined with
the existing source-grounded generation rules. `/script2podcast` keeps the
TXT-script workflow unchanged.

The Web App saves settings and API keys in Telegram SecureStorage on supported
clients (encrypted at rest by the device's secure storage); they are
device-specific and do not sync across devices. If SecureStorage is unavailable,
the Web App saves preferences only and does not persist keys. When starting a
job, the form sends selected credentials over HTTPS to the app. The app
validates them and keeps them in process memory only for the active job/session;
it does not persist them. If the Telegram client cannot open the Web App or
does not support SecureStorage, use **Configure in chat (fallback)** after the
file is checked. In fallback mode keys are entered in chat, deleted where
Telegram allows, and retained only in the active in-memory session. Gemini TTS
requires a Gemini API key; Edge TTS does not.

Use `/keys` or the links in the device settings page to open the official Groq,
OpenAI, and Gemini API key pages. The bot cannot create provider keys for you.
`/help` explains the setup flow, and `/usage` shows the all-user successful
delivery total plus your personal count. A successful delivery is counted only
after Telegram accepts the audio upload. Per-user counts and usernames are
stored in `podcast_generator/telegram/usage.json`; that file is git-ignored.
On Render's free service, runtime file changes are ephemeral and can be lost
on restart or redeploy, so this count can reset. Persisting it across restarts
requires a paid Render persistent disk or an external database.

The webhook queues generation in an in-process background task and returns
quickly. Sessions, credentials, duplicate-update tracking, and queued tasks are
held in memory only. Run a single ASGI worker so webhook updates reach the
same in-memory session store; concurrent user jobs are supported within that
process. For multi-worker or restart-durable deployments, replace the
in-memory session/task store with a shared queue and encrypted ephemeral
credential store. Uploaded inputs, scripts, and MP3s use per-job temporary
directories and are removed after delivery, cancellation, failure, or session
expiry.
