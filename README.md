# Podcast Generation Agent

Generate source-grounded two-speaker podcasts from PDFs or text files with
FastAPI, LangGraph, DSPy, and Edge or Gemini text-to-speech.

## Setup

1. Create and activate a virtual environment.
2. Install the project with `python -m pip install -r requirements.txt`.
3. Copy `.env.example` to `.env` and configure default model IDs, optional model
   lists, and Telegram settings. Never commit `.env`.
4. Ensure FFmpeg is installed and available on `PATH`.

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

Transient Telegram HTTPS connection failures are retried up to five times with
exponential backoff for Bot API calls and file downloads.

In a private chat, send `/start`, choose the LLM provider and model, and provide
your own key when prompted. The bot validates keys and attempts to delete key
messages from the chat. A Gemini key is needed for Gemini TTS; Edge TTS remains
available without it. The bot never uses a server-side provider key.

The webhook queues generation in an in-process background task and returns
quickly. Sessions, credentials, duplicate-update tracking, and queued tasks are
held in memory only. Run a single ASGI worker so webhook updates reach the
same in-memory session store; concurrent user jobs are supported within that
process. For multi-worker or restart-durable deployments, replace the
in-memory session/task store with a shared queue and encrypted ephemeral
credential store. Uploaded inputs, scripts, and MP3s use per-job temporary
directories and are removed after delivery, cancellation, failure, or session
expiry.
