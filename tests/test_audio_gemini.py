import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from podcast_generator.services.audio import synthesize_gemini


class GeminiSynthesisTests(unittest.TestCase):
    def test_request_omits_unsupported_speech_mode(self):
        wav_data = b"RIFF\x00\x00\x00\x00WAVE"

        class Response:
            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

            def read(self):
                return json.dumps(
                    {"output_audio": {"data": base64.b64encode(wav_data).decode()}}
                ).encode()

        with tempfile.TemporaryDirectory() as folder:
            with patch(
                "podcast_generator.services.audio.urllib.request.urlopen",
                return_value=Response(),
            ) as urlopen:
                files = synthesize_gemini(
                    [("HOST", "Hello."), ("EXPERT", "Hi.")],
                    "test-key",
                    "gemini-3.8-flash-tts",
                    "Kore",
                    "Puck",
                    Path(folder),
                )
                audio_bytes = Path(files[0]).read_bytes()

        request = urlopen.call_args.args[0]
        payload = json.loads(request.data)
        speech_config = payload["generation_config"]["speech_config"]
        self.assertNotIn("mode", speech_config)
        self.assertEqual(
            speech_config["speakers"],
            [
                {"speaker": "HOST", "voice": "Kore"},
                {"speaker": "EXPERT", "voice": "Puck"},
            ],
        )
        self.assertEqual(len(files), 1)
        self.assertEqual(audio_bytes, wav_data)


if __name__ == "__main__":
    unittest.main()
