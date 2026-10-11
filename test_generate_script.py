import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)

import generate_script


SCRIPT_RESPONSE = """HOST: Welcome to the episode.
EXPERT: Thanks for inviting me.
HOST: What is the central idea?
EXPERT: It is explained in the source.
"""


class GenerateScriptTests(unittest.TestCase):
    def test_extracts_utf8_text(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.txt"
            source.write_text("Café research notes", encoding="utf-8")

            self.assertEqual(generate_script.extract_source(source), "Café research notes")

    def test_extracts_selectable_pdf_text(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "source.pdf"
            writer = PdfWriter()
            page = writer.add_blank_page(width=300, height=100)

            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            font_ref = writer._add_object(font)
            fonts = DictionaryObject({NameObject("/F1"): font_ref})
            page[NameObject("/Resources")] = DictionaryObject(
                {NameObject("/Font"): fonts}
            )
            content = DecodedStreamObject()
            content.set_data(b"BT /F1 12 Tf 10 50 Td (PDF source text) Tj ET")
            page[NameObject("/Contents")] = writer._add_object(content)
            with source.open("wb") as output:
                writer.write(output)

            self.assertIn("PDF source text", generate_script.extract_source(source))

    def test_loads_provider_keys_from_env_file_without_overriding_environment(self):
        with tempfile.TemporaryDirectory() as folder:
            env_file = Path(folder) / ".env"
            env_file.write_text(
                'GROQ_API_KEY="file-key"\n'
                "GEMINI_API_KEY=gemini-key # comment\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"GROQ_API_KEY": "environment-key"}, clear=True):
                generate_script.load_env_file(env_file)

                self.assertEqual(os.environ["GROQ_API_KEY"], "environment-key")
                self.assertEqual(os.environ["GEMINI_API_KEY"], "gemini-key")

    def test_normalizes_dialogue_and_removes_music_cues(self):
        response = """```markdown
*[Upbeat intro music]*
**HOST:** Welcome.
EXPERT: Thanks for having me.
HOST: What is this about?
**EXPERT:** *The main idea* is clear (upbeat music fades).
# Segment notes
"""
        result = generate_script.normalize_script(response)

        self.assertNotIn("*", result)
        self.assertNotIn("music", result.lower())
        self.assertNotIn("Segment notes", result)
        self.assertEqual(
            result,
            "HOST: Welcome.\n"
            "EXPERT: Thanks for having me.\n"
            "HOST: What is this about?\n"
            "EXPERT: The main idea is clear.\n",
        )

    def test_builds_provider_requests_without_exposing_key_in_url(self):
        groq_request = generate_script._openai_compatible_request(
            "groq", "llama-test", "secret", "Research source", "AI", 5
        )
        gemini_request = generate_script._gemini_request(
            "gemini-test", "secret", "Research source", None, 5
        )

        self.assertEqual(groq_request.full_url, generate_script.PROVIDERS["groq"]["endpoint"])
        self.assertEqual(groq_request.get_header("Authorization"), "Bearer secret")
        self.assertNotIn("secret", gemini_request.full_url)
        self.assertEqual(gemini_request.get_header("X-goog-api-key"), "secret")
        groq_data = groq_request.data
        gemini_data = gemini_request.data
        if not isinstance(groq_data, bytes) or not isinstance(gemini_data, bytes):
            self.fail("Provider request payloads should be JSON bytes.")
        groq_payload = json.loads(groq_data.decode("utf-8"))
        gemini_payload = json.loads(gemini_data.decode("utf-8"))
        self.assertEqual(
            groq_payload["model"],
            "llama-test",
        )
        self.assertEqual(
            gemini_payload["generationConfig"]["maxOutputTokens"],
            6000,
        )

    def test_rejects_missing_speaker_and_ignores_model_preamble(self):
        with self.assertRaisesRegex(ValueError, "both HOST and EXPERT"):
            generate_script.normalize_script("HOST: One line only")
        result = generate_script.normalize_script(
            "Here is your script.\nHOST: Hello\nEXPERT: Welcome.\n"
        )
        self.assertEqual(result, "HOST: Hello\nEXPERT: Welcome.\n")

    def test_reports_truncated_provider_output(self):
        response = {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": "HOST: Start"},
                }
            ]
        }
        with self.assertRaisesRegex(RuntimeError, "token limit"):
            generate_script._response_text("openai", response)

    def test_reads_generated_text_from_each_provider_response_shape(self):
        openai_response = {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {"content": "HOST: Hello\nEXPERT: Hi"},
                }
            ]
        }
        gemini_response = {
            "candidates": [
                {
                    "finishReason": "STOP",
                    "content": {"parts": [{"text": "HOST: Hello\nEXPERT: Hi"}]},
                }
            ]
        }

        self.assertEqual(
            generate_script._response_text("openai", openai_response),
            "HOST: Hello\nEXPERT: Hi",
        )
        self.assertEqual(
            generate_script._response_text("gemini", gemini_response),
            "HOST: Hello\nEXPERT: Hi",
        )

    def test_main_generates_markdown_then_calls_audio_converter(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "notes.txt"
            source.write_text("Research source", encoding="utf-8")
            env_file = Path(folder) / ".env"
            env_file.write_text("", encoding="utf-8")

            with (
                patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}),
                patch(
                    "generate_script.generate_script",
                    return_value=SCRIPT_RESPONSE,
                ) as generate,
                patch(
                    "generate_script.subprocess.run",
                    return_value=type("Result", (), {"returncode": 0})(),
                ) as run,
            ):
                result = generate_script.main(
                    [
                        str(source),
                        "--provider",
                        "groq",
                        "--env-file",
                        str(env_file),
                    ]
                )

            script_path = Path(folder) / "notes_podcast.md"
            self.assertEqual(result, 0)
            self.assertEqual(script_path.read_text(encoding="utf-8"), SCRIPT_RESPONSE)
            self.assertEqual(
                generate.call_args.args[:3],
                ("groq", "llama-3.3-70b-versatile", "test-key"),
            )
            command = run.call_args.args[0]
            self.assertEqual(command[2], str(script_path))
            self.assertIn("+15%", command)
            self.assertIn("en-IN-PrabhatNeural", command)
            self.assertIn("en-IN-NeerjaNeural", command)

    def test_main_does_not_overwrite_existing_output_by_default(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / "notes.txt"
            source.write_text("Research source", encoding="utf-8")
            script_path = Path(folder) / "notes_podcast.md"
            script_path.write_text("Keep this file", encoding="utf-8")
            env_file = Path(folder) / ".env"
            env_file.write_text("", encoding="utf-8")

            with (
                patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}),
                patch("generate_script.generate_script") as generate,
            ):
                result = generate_script.main(
                    [
                        str(source),
                        "--provider",
                        "groq",
                        "--env-file",
                        str(env_file),
                    ]
                )

            self.assertEqual(result, 1)
            self.assertEqual(script_path.read_text(encoding="utf-8"), "Keep this file")
            generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
