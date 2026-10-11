import os
import contextlib
import io
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from unittest.mock import patch

from pypdf import PdfWriter
from pypdf.generic import (
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
)

import generate_script


SCRIPT = "HOST: Welcome.\nEXPERT: Thank you.\nHOST: What is the idea?\nEXPERT: It is clear.\n"


class GenerateScriptTests(unittest.TestCase):
    def create_pdf(self, path: Path, text: str = "Digital PDF text.") -> None:
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
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_ref})}
        )
        content = DecodedStreamObject()
        content.set_data(f"BT /F1 12 Tf 10 50 Td ({text}) Tj ET".encode("ascii"))
        page[NameObject("/Contents")] = writer._add_object(content)
        with path.open("wb") as output:
            writer.write(output)

    def test_extracts_digital_pdf_text_including_page_number(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "digital.pdf"
            self.create_pdf(path)

            text = generate_script.extract_pdf_text(path)

        self.assertIn("[Page 1]", text)
        self.assertIn("Digital PDF text.", text)

    def test_extraction_removes_unmapped_private_use_glyphs(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "digital.pdf"
            self.create_pdf(path, "Question text.")
            with patch(
                "pypdf._page.PageObject.extract_text",
                return_value="\uf06d Question text.",
            ):
                text = generate_script.extract_pdf_text(path)

        self.assertNotIn("\uf06d", text)
        self.assertIn("Question text.", text)

    def test_rejects_pdf_without_selectable_text(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "scanned.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=300, height=100)
            with path.open("wb") as output:
                writer.write(output)

            with self.assertRaisesRegex(ValueError, "No selectable text"):
                generate_script.extract_pdf_text(path)

    def test_resolves_pdf_filename_from_input_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            input_dir = Path(folder)
            source = input_dir / "example.pdf"
            source.touch()
            with patch.object(generate_script, "INPUT_DIR", input_dir):
                resolved = generate_script.resolve_source(Path("example.pdf"))
        self.assertEqual(resolved, source.resolve())

    def test_loads_env_without_overriding_existing_variables(self):
        with tempfile.TemporaryDirectory() as folder:
            env_file = Path(folder) / ".env"
            env_file.write_text(
                'GROQ_API_KEY="file-key"\nGROQ_MODEL=custom-model\n',
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"GROQ_API_KEY": "existing-key"}, clear=True):
                generate_script.load_env_file(env_file)
                self.assertEqual(os.environ["GROQ_API_KEY"], "existing-key")
                self.assertEqual(os.environ["GROQ_MODEL"], "custom-model")

    def test_normalizes_only_host_and_expert_lines(self):
        raw = "A preamble\nHOST: Welcome.\nEXPERT: *Hello.*\n"
        self.assertEqual(
            generate_script.normalize_script(raw),
            "HOST: Welcome.\nEXPERT: Hello.\n",
        )

    def test_rejects_script_missing_one_speaker(self):
        with self.assertRaisesRegex(ValueError, "HOST and EXPERT"):
            generate_script.normalize_script("HOST: Welcome.")

    def test_generate_script_uses_non_reserved_dspy_signature_field(self):
        import dspy

        predictor = Mock(return_value=SimpleNamespace(dialogue=SCRIPT))
        with (
            patch.object(dspy, "LM", return_value=object()),
            patch.object(dspy, "context", return_value=nullcontext()),
            patch.object(dspy, "Predict", return_value=predictor),
        ):
            result = generate_script.generate_script(
                "groq", "test-model", "test-key", "source text", None
            )

        self.assertEqual(result, SCRIPT)
        self.assertEqual(predictor.call_args.kwargs["guidelines"],
                         generate_script.SYSTEM_PROMPT)
        self.assertNotIn("instructions", predictor.call_args.kwargs)

    def test_system_prompt_requests_conversational_spoken_script(self):
        self.assertIn("do not mechanically read", generate_script.SYSTEM_PROMPT)
        self.assertIn("follow-up questions", generate_script.SYSTEM_PROMPT)
        self.assertIn("Vary the length and rhythm of turns", generate_script.SYSTEM_PROMPT)

    def test_cli_help_is_rendered(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            with self.assertRaises(SystemExit) as error:
                generate_script.parse_args(["--help"])
        self.assertEqual(error.exception.code, 0)
        self.assertIn("digital-text PDF", output.getvalue())

    def test_main_writes_script_and_calls_audio_converter(self):
        with tempfile.TemporaryDirectory() as folder:
            folder_path = Path(folder)
            source = folder_path / "source.pdf"
            self.create_pdf(source)
            output_dir = folder_path / "output"
            with (
                patch.dict(
                    os.environ,
                    {
                        "GROQ_API_KEY": "test-key",
                        "GROQ_MODEL": "custom-groq-model",
                    },
                    clear=True,
                ),
                patch(
                    "generate_script.generate_script",
                    return_value=SCRIPT,
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
                        "--output-dir",
                        str(output_dir),
                    ]
                )

            script_path = output_dir / "source_podcast.md"
            audio_path = output_dir / "source_podcast.mp3"
            self.assertEqual(result, 0)
            self.assertEqual(script_path.read_text(encoding="utf-8"), SCRIPT)
            self.assertEqual(
                generate.call_args.args,
                ("groq", "custom-groq-model", "test-key", 
                 generate_script.extract_pdf_text(source), None),
            )
            command = run.call_args.args[0]
            self.assertEqual(command[2], str(script_path))
            self.assertEqual(command[3], str(audio_path))
            self.assertIn("+15%", command)
            self.assertIn("--tts-provider", command)
            self.assertEqual(command[command.index("--tts-provider") + 1], "edge")

    def test_main_refuses_to_overwrite_without_flag(self):
        with tempfile.TemporaryDirectory() as folder:
            folder_path = Path(folder)
            source = folder_path / "source.pdf"
            self.create_pdf(source)
            output_dir = folder_path / "output"
            output_dir.mkdir()
            existing = output_dir / "source_podcast.md"
            existing.write_text("keep this", encoding="utf-8")
            with (
                patch.dict(os.environ, {"GROQ_API_KEY": "test-key"}, clear=True),
                patch("generate_script.generate_script") as generate,
            ):
                result = generate_script.main(
                    [
                        str(source),
                        "--provider",
                        "groq",
                        "--output-dir",
                        str(output_dir),
                    ]
                )

            self.assertEqual(result, 1)
            self.assertEqual(existing.read_text(encoding="utf-8"), "keep this")
            generate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
