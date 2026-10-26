import sys
import types
import unittest
from contextlib import nullcontext
from unittest.mock import patch

from podcast_generator.services import scripts


class ScriptGenerationTests(unittest.TestCase):
    def test_large_source_is_split_and_segments_are_combined(self):
        source_text = "\n\n".join(
            f"Paragraph {index}. " + ("Source detail. " * 700)
            for index in range(3)
        )
        calls = []
        responses = iter(
            [
                "HOST: First host turn.\nEXPERT: First expert turn.",
                "HOST: Second host turn.\nEXPERT: Second expert turn.",
                "HOST: Third host turn.\nEXPERT: Third expert turn.",
            ]
        )

        class FakeSignature:
            pass

        class FakePredict:
            def __init__(self, _signature):
                pass

            def __call__(self, **kwargs):
                calls.append(kwargs)
                return types.SimpleNamespace(dialogue=next(responses))

        fake_dspy = types.ModuleType("dspy")
        fake_dspy.Signature = FakeSignature
        fake_dspy.InputField = lambda: None
        fake_dspy.OutputField = lambda: None
        fake_dspy.LM = lambda *_args, **kwargs: kwargs
        fake_dspy.Predict = FakePredict
        fake_dspy.context = lambda **_kwargs: nullcontext()

        with patch.dict(sys.modules, {"dspy": fake_dspy}):
            result = scripts.generate_script(
                "groq", "test-model", "test-key", source_text, None
            )

        self.assertEqual(len(calls), 3)
        self.assertTrue(
            all(len(call["source_text"]) <= scripts.SOURCE_CHUNK_CHARACTERS
                for call in calls)
        )
        self.assertTrue(all("one segment" in call["guidelines"] for call in calls))
        self.assertEqual(
            result.count("HOST:"),
            3,
        )
        self.assertIn("Third expert turn.", result)

    def test_small_source_uses_one_prompt_without_segment_guidelines(self):
        calls = []

        class FakeSignature:
            pass

        class FakePredict:
            def __init__(self, _signature):
                pass

            def __call__(self, **kwargs):
                calls.append(kwargs)
                return types.SimpleNamespace(
                    dialogue="HOST: Hello.\nEXPERT: Welcome."
                )

        fake_dspy = types.ModuleType("dspy")
        fake_dspy.Signature = FakeSignature
        fake_dspy.InputField = lambda: None
        fake_dspy.OutputField = lambda: None
        fake_dspy.LM = lambda *_args, **kwargs: kwargs
        fake_dspy.Predict = FakePredict
        fake_dspy.context = lambda **_kwargs: nullcontext()

        with patch.dict(sys.modules, {"dspy": fake_dspy}):
            result = scripts.generate_script(
                "groq", "test-model", "test-key", "Short source.", None
            )

        self.assertEqual(len(calls), 1)
        self.assertNotIn("one segment", calls[0]["guidelines"])
        self.assertEqual(result, "HOST: Hello.\nEXPERT: Welcome.\n")

    def test_language_model_output_limit_is_provider_safe(self):
        self.assertEqual(scripts.MAX_SCRIPT_OUTPUT_TOKENS, 4096)


if __name__ == "__main__":
    unittest.main()
