import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import make_mp3
import generate_script


class GeminiTtsTests(unittest.TestCase):
    def test_direct_audio_command_can_load_gemini_key_from_project_env(self):
        with tempfile.TemporaryDirectory() as folder:
            project_dir = Path(folder)
            (project_dir / ".env").write_text(
                "GEMINI_API_KEY=test-key\n",
                encoding="utf-8",
            )
            with (
                patch.object(generate_script, "PROJECT_DIR", project_dir),
                patch.dict("os.environ", {}, clear=True),
            ):
                self.assertEqual(make_mp3.load_gemini_api_key(), "test-key")

    def test_synthesizes_two_speaker_dialogue_with_one_native_request(self):
        wav_data = b"RIFF\x04\x00\x00\x00WAVE"
        response_data = json.dumps(
            {"output_audio": {"data": base64.b64encode(wav_data).decode("ascii")}}
        ).encode("utf-8")
        turns = [
            ("HOST", "What does this mean?"),
            ("EXPERT", "It means the two voices are synthesized together."),
        ]

        with tempfile.TemporaryDirectory() as folder:
            with patch(
                "make_mp3.urllib.request.urlopen",
                return_value=io.BytesIO(response_data),
            ) as urlopen:
                paths = make_mp3.synthesize_gemini(
                    turns,
                    "test-api-key",
                    "gemini-3.8-flash-tts",
                    "Kore",
                    "Puck",
                    Path(folder),
                )

            self.assertEqual(len(paths), 1)
            self.assertEqual(paths[0].read_bytes(), wav_data)
            request = urlopen.call_args.args[0]
            body = json.loads(request.data)
            self.assertEqual(body["model"], "gemini-3.8-flash-tts")
            self.assertEqual(body["generation_config"]["speech_config"]["mode"],
                             "conversational")
            self.assertEqual(
                body["generation_config"]["speech_config"]["speakers"],
                [
                    {"speaker": "HOST", "voice": "Kore"},
                    {"speaker": "EXPERT", "voice": "Puck"},
                ],
            )
            items = body["input"][0]["content"]
            self.assertEqual(items[0]["annotations"][0]["speaker"], "HOST")
            self.assertEqual(items[1]["annotations"][0]["speaker"], "EXPERT")
            for item in items:
                style = item["annotations"][0]["style"]
                self.assertIn("Indian English", style)
                self.assertIn("do not exaggerate", style)

    def test_gemini_tts_requires_two_speakers(self):
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(make_mp3.SynthesisError, "exactly two"):
                make_mp3.synthesize_gemini(
                    [("HOST", "One speaker only.")],
                    "test-api-key",
                    "gemini-3.8-flash-tts",
                    "Kore",
                    "Puck",
                    Path(folder),
                )


if __name__ == "__main__":
    unittest.main()
