# Podcast Generation Agent

Turn a PDF or text source into a source-grounded, two-speaker podcast script
and an MP3. The project includes a FastAPI service, a command-line generator,
and a Telegram bot.

> **Telegram voice provider:** Telegram always uses Google Gemini TTS and
> requires a Gemini API key. The REST API and command-line interface continue
> to offer both Gemini TTS and Edge TTS.

## What it does

- Extracts text from digital PDFs or UTF-8 text files.
- Generates a conversational `HOST` / `EXPERT` script with a selected LLM
  provider (Groq, Gemini, or OpenAI).
- Splits long documents into sections and carries covered topics and recent
  dialogue forward to reduce repeated introductions and improve continuity.
- Excludes clearly marked references and page markers from narration.
- Synthesizes speech and combines it into an MP3.
- Sends both the Markdown script and MP3 through Telegram. The script is sent
  first; the job's temporary files are cleaned up afterward.

## Requirements

- Python 3.10 or newer (the included Docker image uses Python 3.11).
- FFmpeg available on `PATH` for local audio generation. The Docker image
  installs FFmpeg.
- A provider API key for script generation. Telegram users supply their own
  key in the device settings page or in the private bot chat.
- A Gemini API key whenever Gemini TTS is selected; it is always required by
  Telegram.

## Install and run locally

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
Copy-Item .env.example .env
```

Install FFmpeg if it is not already available. Add provider keys to `.env` only
for local CLI use; do not commit that file. Telegram and REST API requests use
the keys supplied by each user rather than a server-side provider key.

Start the REST API:

```powershell
uvicorn podcast_generator.api.main:app --host 0.0.0.0 --port 8000
```

- Interactive API documentation: `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`
- Provider/model catalog: `http://localhost:8000/providers`

The REST API accepts PDF uploads at `POST /podcasts`. Send the script provider,
its `api_key`, and the PDF as multipart form data. For Gemini TTS, also provide
`gemini_api_key` unless the selected script provider is Gemini, in which case
the same key can be used. The response includes job-specific script and audio
download URLs.

## Command-line generation

The CLI reads the script-provider key from `.env` (for example
`GROQ_API_KEY`, `GEMINI_API_KEY`, or `OPENAI_API_KEY`) and writes outputs to
`output/` by default:

```powershell
python -m podcast_generator input\source.pdf --provider groq --tts-provider gemini
```

Use `--help` to see options including custom models, topic guidance, voice IDs,
output directory, overwrite behavior, and TTS model selection. CLI TTS defaults
to Edge; use `--tts-provider gemini` to select Gemini TTS. Gemini TTS requires
a Gemini key in `.env`.

## Telegram bot

The Telegram integration runs as a webhook on the FastAPI application. For
installation, bot configuration, webhook registration, commands, file limits,
and a step-by-step usage walkthrough, see the
[Telegram user and setup guide](docs/TELEGRAM_GUIDE.md).

Quick overview:

1. Configure the Telegram webhook and open the bot in a private chat.
2. Send `/start`, `/pdf2podcast`, or `/script2podcast`, then upload a PDF or TXT
   document as requested.
3. Configure a script provider and key. Google Gemini TTS also needs a Gemini
   key; when Gemini is the script provider, that key serves both jobs.
4. The bot sends the generated Markdown script, then the audio.

Telegram sessions, credentials, active jobs, and temporary files are
process-local. Use one Uvicorn worker. A restart clears active sessions and
temporary files; the deployment's filesystem may also be ephemeral.

## Configuration

Copy `.env.example` to `.env` for local settings. Telegram requires:

| Variable | Purpose |
| --- | --- |
| `TELEGRAM_BOT_TOKEN` | Bot token from BotFather. |
| `TELEGRAM_WEBHOOK_SECRET` | Secret used to authenticate webhook requests. Use 1-256 letters, digits, `_`, or `-`. |
| `TELEGRAM_WEBHOOK_URL` | Public HTTPS webhook URL ending in `/telegram/webhook`. |
| `GEMINI_TTS_MODEL` | Optional Google Gemini TTS model override. |
| `TELEGRAM_MAX_FILE_SIZE` | Maximum Telegram upload size in bytes; default 20 MiB. |
| `TELEGRAM_MAX_TEXT_CHARACTERS` | Maximum extracted/input text; default 200,000 characters. |
| `TELEGRAM_MAX_AUDIO_SIZE` | Maximum audio upload size in bytes; default 50 MiB. |
| `TELEGRAM_SESSION_TTL_SECONDS` | Idle session expiration; default 1,800 seconds. |
| `TELEGRAM_JOB_TIMEOUT_SECONDS` | Script and audio job time budget; default 1,800 seconds. |

`GROQ_MODEL`, `OPENAI_MODEL`, and `GEMINI_MODEL` set script-model defaults.
Comma-separated `*_MODELS` variables add choices to the API and settings page.
Provider keys should not be committed or placed in shared deployment
configuration for Telegram jobs.

## Deployment

The repository includes a Render Blueprint (`render.yaml`) and a Dockerfile.
Create a Render Blueprint from the repository, then set the Telegram
environment variables in the Render service dashboard. Set
`TELEGRAM_WEBHOOK_URL` to
`https://<your-service>.onrender.com/telegram/webhook`.

After deployment, register the webhook with the same token, secret, and URL in
your local `.env`:

```powershell
python -m podcast_generator.telegram.set_webhook
```

The registration command also refreshes the bot's Telegram command menu. The
container runs one Uvicorn worker to keep process-local sessions consistent.
Render's free service may sleep and uses an ephemeral filesystem; active work
and local delivery statistics do not survive restarts or redeploys.

## Security and privacy notes

- Do not commit `.env` or paste provider keys into public chats.
- The bot asks users to provide their own LLM key. Telegram key messages are
  deleted automatically where Telegram permits; if deletion fails, the bot
  asks the user to delete the message manually.
- On supported Telegram clients, the settings page stores keys in that
  device's Telegram SecureStorage. Keys are sent over HTTPS for a job and kept
  in server process memory only. They are not written to server-side settings.
- Uploaded files, generated scripts, and audio use temporary job directories
  in Telegram and are cleaned up after delivery or failure.

## Project layout

```text
podcast_generator/
  api/                 FastAPI endpoints
  services/            PDF/script and speech generation
  telegram/            Webhook bot, settings page, and Telegram client
  tools/               LangGraph workflow tools
  workflows/           Script-then-audio orchestration
input/                 Local input files and podcast preferences
output/                Local CLI/API outputs
tests/                 Unit and integration tests
docs/                  User documentation and illustrations
```

## Tests

Run the test suite from the repository root:

```powershell
python -m unittest discover -s tests -v
```
