#!/usr/bin/env python3
"""Convert a UTF-8 text or Markdown file into an MP3 using Edge TTS.

Setup (once):   pip install edge-tts      # also needs ffmpeg on PATH
Run:            python make_mp3.py input.md output.mp3
Speed:          --rate -10% slows speech; --rate +10% speeds it up
Two speakers:   --speaker-1-voice en-IN-PrabhatNeural --speaker-2-voice en-IN-NeerjaNeural
"""
import argparse
import asyncio
import base64
import binascii
import re
import shutil
import subprocess
import sys
import tempfile
import json
import os
import urllib.error
import urllib.request
from pathlib import Path


def clean(raw: str) -> str:
    out = []
    in_code = False
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith("```"):
            if not in_code:
                out.append("A code example is omitted.")
            in_code = not in_code
            continue
        if in_code:
            continue
        if not s:
            out.append("")
            continue
        if re.fullmatch(r"\|?[\s\-|:]+\|?", s):          # table separator row
            continue
        if s.startswith("|"):                              # table row -> comma list
            s = ", ".join(c.strip() for c in s.strip("|").split("|") if c.strip())
        s = re.sub(r"!\[([^\]]*)\]\([^)]+\)", r"\1", s) # image -> alt text
        s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)  # link -> label
        s = re.sub(r"^#+\s*", "", s)                       # headings
        s = re.sub(r"^[*>\-]\s+", "", s)                   # bullets / quotes
        s = re.sub(r"^(\d+)\\\.", r"\1.", s)
        s = s.replace("*", "").replace("`", "")
        s = s.replace("→", " to ").replace("&", " and ")
        out.append(s)
    text = "\n".join(out)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def chunks(text: str, limit: int = 2500):
    if limit < 1:
        raise ValueError("chunk limit must be at least 1")

    remaining = text.strip()
    while remaining:
        if len(remaining) <= limit:
            yield remaining
            break

        window = remaining[:limit]
        sentence_ends = [
            match.end() - 1
            for match in re.finditer(r"""[.!?]["')\]]?\s""", window)
        ]
        sentence_boundary = max(sentence_ends, default=0)
        if sentence_boundary >= limit * 0.6:
            boundary = sentence_boundary
        else:
            boundary = max(
                window.rfind(" "),
                window.rfind("\n"),
                window.rfind("\t"),
            )

        if boundary <= 0:
            boundary = limit

        chunk = remaining[:boundary].strip()
        if chunk:
            yield chunk
        remaining = remaining[boundary:].strip()


def dialogue_turns(raw: str):
    speaker_line = re.compile(
        r"^\s*(?:\*\*|__)?([A-Za-z][A-Za-z0-9 _.'-]*?):(?:\*\*|__)?\s*(.*)$"
    )
    lines = []
    speakers = []
    for line in raw.splitlines():
        match = speaker_line.match(line)
        speaker = match.group(1).strip() if match else None
        if speaker and speaker not in speakers:
            speakers.append(speaker)
        lines.append((speaker, match.group(2) if match else line))

    if len(speakers) < 2:
        return None
    if len(speakers) > 2:
        raise ValueError(
            "input contains more than two speakers; this converter supports "
            "two-speaker dialogue"
        )

    turns = []
    current_speaker = speakers[0]
    for speaker, text in lines:
        if speaker:
            current_speaker = speaker
        if turns and turns[-1][0] == current_speaker:
            turns[-1] = (current_speaker, f"{turns[-1][1]}\n{text}")
        else:
            turns.append((current_speaker, text))
    return turns


def _group_turns(turns, limit: int = 12000):
    """Group dialogue turns into Gemini requests without splitting sentences."""
    grouped = []
    current = []
    current_length = 0
    for speaker, text in turns:
        for part in chunks(text, limit):
            if current and current_length + len(part) > limit:
                grouped.append(current)
                current = []
                current_length = 0
            current.append((speaker, part))
            current_length += len(part)
    if current:
        grouped.append(current)
    return grouped


def _gemini_audio_bytes(response: dict) -> bytes:
    """Extract WAV data from Gemini Interactions API response formats."""
    output_audio = response.get("output_audio")
    if isinstance(output_audio, dict) and isinstance(output_audio.get("data"), str):
        return base64.b64decode(output_audio["data"], validate=True)

    for step in reversed(response.get("steps") or []):
        for item in step.get("content") or []:
            if item.get("type") == "audio" and isinstance(item.get("data"), str):
                return base64.b64decode(item["data"], validate=True)
    raise SynthesisError("Gemini TTS response did not include audio data.")


def synthesize_gemini(
    turns,
    api_key: str,
    model: str,
    speaker_1_voice: str,
    speaker_2_voice: str,
    tmp: Path,
):
    """Synthesize speaker-tagged dialogue with Gemini's multi-speaker TTS."""
    speakers = list(dict.fromkeys(speaker for speaker, _ in turns))
    if len(speakers) != 2:
        raise SynthesisError(
            "Gemini TTS requires dialogue containing exactly two speakers."
        )
    voices = {
        speakers[0]: speaker_1_voice,
        speakers[1]: speaker_2_voice,
    }
    speaker_styles = {
        speakers[0]: (
            "Curious, warm podcast host speaking natural Indian English with "
            "a clear, authentic Indian English accent. Engaged and relaxed; "
            "do not exaggerate the accent."
        ),
        speakers[1]: (
            "Warm, thoughtful expert speaking natural Indian English with "
            "a clear, authentic Indian English accent. Confident and relaxed; "
            "do not exaggerate the accent."
        ),
    }

    files = []
    groups = _group_turns(turns)
    for index, group in enumerate(groups, start=1):
        content = []
        for speaker, text in group:
            content.append(
                {
                    "type": "text",
                    "text": text,
                    "annotations": [
                        {
                            "type": "speech_metadata",
                            "speaker": speaker,
                            "style": speaker_styles[speaker],
                        }
                    ],
                }
            )
        body = {
            "model": model,
            "input": [{"type": "user_input", "content": content}],
            "response_format": {"type": "audio"},
            "generation_config": {
                "speech_config": {
                    "mode": "conversational",
                    "speakers": [
                        {"speaker": speaker, "voice": voices[speaker]}
                        for speaker in speakers
                    ],
                }
            },
        }
        request = urllib.request.Request(
            "https://generativelanguage.googleapis.com/v1beta/interactions",
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=180) as response:
                try:
                    response_data = json.loads(response.read().decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise SynthesisError(
                        "Gemini TTS returned an invalid JSON response."
                    ) from exc
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            try:
                error_body = json.loads(detail)
                detail = error_body.get("error", {}).get("message", detail)
            except json.JSONDecodeError:
                pass
            raise SynthesisError(
                f"Gemini TTS request failed (HTTP {exc.code}): {detail}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise SynthesisError(f"Could not connect to Gemini TTS: {exc}") from exc

        try:
            audio = _gemini_audio_bytes(response_data)
        except (ValueError, binascii.Error) as exc:
            raise SynthesisError(
                "Gemini TTS returned malformed audio data."
            ) from exc
        if not audio.startswith(b"RIFF") or audio[8:12] != b"WAVE":
            raise SynthesisError("Gemini TTS returned audio that is not a WAV file.")
        path = tmp / f"part_{index:03d}.wav"
        path.write_bytes(audio)
        files.append(path)
        print(f"  synthesized {index}/{len(groups)}", flush=True)
    return files


class SynthesisError(RuntimeError):
    """Raised when a text chunk cannot be synthesized after retries."""


MAX_SYNTHESIS_ATTEMPTS = 20
MAX_RETRY_DELAY_SECONDS = 30


def load_gemini_api_key() -> str:
    """Load the Gemini key from the project .env when not already in the environment."""
    from generate_script import PROJECT_DIR, load_env_file

    load_env_file(PROJECT_DIR / ".env")
    return os.environ.get("GEMINI_API_KEY", "").strip()


async def synth(parts, rate, tmp: Path):
    import aiohttp
    import edge_tts

    files = []
    for i, (p, voice) in enumerate(parts):
        f = tmp / f"part_{i:03d}.mp3"
        for attempt in range(MAX_SYNTHESIS_ATTEMPTS):
            try:
                await edge_tts.Communicate(p, voice, rate=rate).save(str(f))
                break
            except (aiohttp.ClientConnectionError, asyncio.TimeoutError):
                if attempt == MAX_SYNTHESIS_ATTEMPTS - 1:
                    raise SynthesisError(
                        f"Could not synthesize chunk {i + 1}/{len(parts)} after "
                        f"{MAX_SYNTHESIS_ATTEMPTS} attempts. Check your internet "
                        "connection and access to speech.platform.bing.com, then "
                        "try again."
                    )
                delay = min(2 ** attempt, MAX_RETRY_DELAY_SECONDS)
                print(
                    f"  connection failed for chunk {i + 1}; "
                    f"retrying in {delay}s "
                    f"({attempt + 1}/{MAX_SYNTHESIS_ATTEMPTS - 1})",
                    flush=True,
                )
                await asyncio.sleep(delay)
        files.append(f)
        print(f"  synthesized {i + 1}/{len(parts)}", flush=True)
    return files


def main():
    ap = argparse.ArgumentParser(
        description="Convert a UTF-8 text or Markdown file to an MP3 with Edge TTS.",
        epilog=(
            "Examples: --rate -10% slows speech; --rate +10% speeds it up. "
            "Use --voice en-IN-PrabhatNeural for a male Indian-English voice."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("src"); ap.add_argument("dst", nargs="?", default="output.mp3")
    ap.add_argument(
        "--voice",
        default="en-IN-NeerjaNeural",
        help="Edge TTS voice for non-dialogue input (default: en-IN-NeerjaNeural)",
    )
    ap.add_argument(
        "--tts-provider",
        choices=("edge", "gemini"),
        default="edge",
        help="speech synthesis backend (default: edge)",
    )
    ap.add_argument(
        "--tts-model",
        default="gemini-3.8-flash-tts",
        help="Gemini TTS model (default: gemini-3.8-flash-tts)",
    )
    ap.add_argument(
        "--speaker-1-voice",
        help="voice for the first detected speaker",
    )
    ap.add_argument(
        "--speaker-2-voice",
        help="voice for the second detected speaker",
    )
    ap.add_argument(
        "--rate",
        default="+0%",
        help="speech speed adjustment, such as -10%% or +10%% (default: +0%%)",
    )
    ap.add_argument(
        "--text-only",
        action="store_true",
        help="write cleaned speech text to a .clean.txt file without synthesizing",
    )
    args = sys.argv[1:]
    normalized_args = []
    i = 0
    while i < len(args):
        if (
            args[i] == "--rate"
            and i + 1 < len(args)
            and re.fullmatch(r"-\d+%", args[i + 1])
        ):
            normalized_args.append(f"--rate={args[i + 1]}")
            i += 2
        else:
            normalized_args.append(args[i])
            i += 1
    a = ap.parse_args(normalized_args)

    if not re.fullmatch(r"[+-]\d+%", a.rate):
        ap.error("--rate must be a signed percentage, for example -10% or +10%")

    raw = Path(a.src).read_text(encoding="utf-8-sig")
    try:
        turns = dialogue_turns(raw)
    except ValueError as exc:
        ap.error(str(exc))

    if a.tts_provider == "gemini":
        if not turns:
            ap.error("Gemini TTS requires a two-speaker HOST/EXPERT script")
        try:
            gemini_api_key = load_gemini_api_key()
        except ValueError as exc:
            ap.error(str(exc))
        if not gemini_api_key:
            ap.error("GEMINI_API_KEY is required for --tts-provider gemini")
        assert turns is not None
        speaker_1_voice = a.speaker_1_voice or "Kore"
        speaker_2_voice = a.speaker_2_voice or "Puck"
    else:
        speaker_1_voice = a.speaker_1_voice or "en-IN-PrabhatNeural"
        speaker_2_voice = a.speaker_2_voice or "en-IN-NeerjaNeural"
    gemini_turns = turns if turns is not None else []

    if turns:
        speaker_voices = {
            turns_speaker: voice
            for turns_speaker, voice in zip(
                dict.fromkeys(speaker for speaker, _ in turns),
                (speaker_1_voice, speaker_2_voice),
            )
        }
        parts = [
            (part, speaker_voices[speaker])
            for speaker, turn_text in turns
            for part in chunks(clean(turn_text))
            if part
        ]
        text = "\n".join(clean(turn_text) for _, turn_text in turns).strip()
        print(
            "detected two speakers: "
            + ", ".join(speaker_voices)
            + f" ({speaker_1_voice}, {speaker_2_voice})"
        )
    else:
        text = clean(raw)
        parts = [(part, a.voice) for part in chunks(text)]

    if not text:
        ap.error("input contains no text to synthesize")
    if a.text_only:
        Path(a.dst).with_suffix(".clean.txt").write_text(text, encoding="utf-8")
        print(f"cleaned: {len(text)} chars, {len(text.split())} words")
        return
    if not shutil.which("ffmpeg"):
        print("error: ffmpeg was not found; install it and add it to PATH", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        try:
            if a.tts_provider == "gemini":
                files = synthesize_gemini(
                    [(speaker, clean(turn_text)) for speaker, turn_text in gemini_turns],
                    gemini_api_key,
                    a.tts_model,
                    speaker_1_voice,
                    speaker_2_voice,
                    tmp,
                )
            else:
                files = asyncio.run(synth(parts, a.rate, tmp))
        except SynthesisError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

        lst = tmp / "list.txt"
        lst.write_text("".join(f"file '{f}'\n" for f in files), encoding="utf-8")
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0",
                        "-i", str(lst), "-c:a", "libmp3lame", "-b:a", "48k", "-ar", "24000",
                        "-ac", "1", "-f", "mp3", a.dst], check=True)
    print("saved", a.dst)


if __name__ == "__main__":
    sys.exit(main())
