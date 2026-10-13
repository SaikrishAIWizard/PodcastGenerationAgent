"""FastAPI service for generating podcast scripts and audio from PDF uploads."""

import re
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel

from ..settings import (
    OUTPUT_DIR,
    PREFERENCES_FILE,
    PROJECT_DIR,
    PROVIDERS,
    load_env_file,
    load_preferences,
    provider_models,
)
from ..telegram.client import ProviderValidationError, validate_provider_key
from ..telegram.router import router as telegram_router
from ..telegram.usage import UsageStoreError, get_usage
from ..workflows.podcast import podcast_graph


app = FastAPI(
    title="Podcast Generation API",
    description="Generate customized podcast scripts and audio from PDF uploads.",
    version="1.0.0",
)
app.include_router(telegram_router)
jobs: dict[str, tuple[Path, Path]] = {}


@app.get("/health")
@app.get("/keep-alive")
async def health_check() -> dict[str, str]:
    """Report that the service is responding to Render and uptime monitors."""
    return {"status": "ok"}


@app.get("/usage")
async def usage_totals() -> dict[str, int]:
    """Return the global count of successfully delivered Telegram podcasts."""
    try:
        counts = await get_usage()
        return {
            "total_successful_deliveries": counts[
                "total_successful_deliveries"
            ]
        }
    except UsageStoreError as exc:
        raise HTTPException(
            status_code=503,
            detail="Usage totals are temporarily unavailable.",
        ) from exc


class PodcastResponse(BaseModel):
    job_id: str
    script_url: str
    audio_url: str


def _redact_credentials(message: str, *credentials: str) -> str:
    """Prevent provider credentials from appearing in API error responses."""
    for credential in credentials:
        if credential:
            message = message.replace(credential, "[redacted]")
    return message


@app.post("/podcasts", response_model=PodcastResponse, status_code=201)
async def create_podcast(
    file: UploadFile = File(...),
    provider: Literal["groq", "gemini", "openai"] = Form(...),
    api_key: str = Form(
        ..., min_length=1, json_schema_extra={"format": "password"}
    ),
    gemini_api_key: str = Form("", json_schema_extra={"format": "password"}),
    model: str = Form(""),
    topic: str = Form(""),
    conversation_style: str = Form(""),
    suggestions: str = Form(""),
    tts_provider: Literal["edge", "gemini"] = Form("edge"),
    tts_model: str = Form(""),
    speaker_1_voice: str = Form(""),
    speaker_2_voice: str = Form(""),
    rate: str = Form("+15%"),
) -> PodcastResponse:
    """Accept a PDF upload and run the complete LangGraph podcast workflow."""
    load_env_file(PROJECT_DIR / ".env")
    if not file.filename or Path(file.filename.replace("\\", "/")).suffix.casefold() != ".pdf":
        await file.close()
        raise HTTPException(status_code=400, detail="Upload a PDF file.")

    api_key = api_key.strip()
    gemini_api_key = gemini_api_key.strip()
    model = model.strip()
    if not api_key:
        raise HTTPException(status_code=422, detail="Enter your provider API key.")
    if model and not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}", model):
        raise HTTPException(status_code=422, detail="Enter a valid model identifier.")
    default_model, _ = provider_models(provider)
    selected_model = model or default_model
    if tts_provider == "gemini":
        if provider == "gemini":
            gemini_api_key = gemini_api_key or api_key
        if not gemini_api_key:
            raise HTTPException(
                status_code=422,
                detail="Gemini TTS requires your Gemini API key.",
            )

    try:
        await validate_provider_key(provider, api_key)
        if tts_provider == "gemini" and provider != "gemini":
            await validate_provider_key("gemini", gemini_api_key)
    except ProviderValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    preferences = load_preferences(PREFERENCES_FILE)
    job_id = uuid.uuid4().hex
    original_stem = Path(file.filename.replace("\\", "/")).stem
    safe_stem = re.sub(r"[^A-Za-z0-9_-]+", "_", original_stem).strip("_")[:80]
    output_stem = f"{safe_stem or 'podcast'}_{job_id[:8]}"
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    script_path = OUTPUT_DIR / f"{output_stem}_podcast.md"
    audio_path = OUTPUT_DIR / f"{output_stem}_podcast.mp3"

    try:
        with tempfile.TemporaryDirectory(prefix="podcast-upload-") as temp_dir:
            source_path = Path(temp_dir) / "upload.pdf"
            with source_path.open("wb") as destination:
                shutil.copyfileobj(file.file, destination)

            result = await run_in_threadpool(
                podcast_graph.invoke,
                {
                    "source_pdf": str(source_path),
                    "provider": provider,
                    "api_key": api_key,
                    "gemini_api_key": gemini_api_key,
                    "model": selected_model,
                    "topic": topic,
                    "conversation_style": (
                        conversation_style or preferences["conversation_style"]
                    ),
                    "suggestions": suggestions or preferences["suggestions"],
                    "tts_provider": tts_provider,
                    "tts_model": tts_model,
                    "speaker_1_voice": (
                        speaker_1_voice or preferences["speaker_1_voice"]
                    ),
                    "speaker_2_voice": (
                        speaker_2_voice or preferences["speaker_2_voice"]
                    ),
                    "rate": rate,
                    "script_output_path": str(script_path),
                    "audio_output_path": str(audio_path),
                },
            )
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=_redact_credentials(str(exc), api_key, gemini_api_key),
        ) from exc
    except RuntimeError as exc:
        raise HTTPException(
            status_code=502,
            detail=_redact_credentials(str(exc), api_key, gemini_api_key),
        ) from exc
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail=_redact_credentials(str(exc), api_key, gemini_api_key),
        ) from exc
    finally:
        await file.close()

    jobs[job_id] = (Path(result["script_path"]), Path(result["audio_path"]))
    return PodcastResponse(
        job_id=job_id,
        script_url=f"/podcasts/{job_id}/script",
        audio_url=f"/podcasts/{job_id}/audio",
    )


@app.get("/providers")
async def list_providers() -> dict[str, dict[str, object]]:
    """List providers and configured model choices for API clients."""
    load_env_file(PROJECT_DIR / ".env")
    return {
        provider: {
            "default_model": provider_models(provider)[0],
            "models": provider_models(provider)[1],
        }
        for provider in PROVIDERS
    }


@app.get("/podcasts/{job_id}/script")
async def download_script(job_id: str) -> FileResponse:
    """Download a script generated by this API process."""
    job = jobs.get(job_id)
    if job is None or not job[0].is_file():
        raise HTTPException(status_code=404, detail="Podcast job was not found.")
    return FileResponse(
        job[0],
        media_type="text/markdown",
        filename=job[0].name,
    )


@app.get("/podcasts/{job_id}/audio")
async def download_audio(job_id: str) -> FileResponse:
    """Download the MP3 generated by this API process."""
    job = jobs.get(job_id)
    if job is None or not job[1].is_file():
        raise HTTPException(status_code=404, detail="Podcast job was not found.")
    return FileResponse(
        job[1],
        media_type="audio/mpeg",
        filename=job[1].name,
    )
