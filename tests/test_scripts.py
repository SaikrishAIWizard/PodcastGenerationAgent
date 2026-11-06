import sys
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
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
                return SimpleNamespace(
                    dialogue=next(responses),
                    coverage_notes=f"Covered concept {len(calls)}.",
                )

        fake_dspy = SimpleNamespace(
            Signature=FakeSignature,
            InputField=lambda: None,
            OutputField=lambda: None,
            LM=lambda *_args, **kwargs: kwargs,
            Predict=FakePredict,
            context=lambda **_kwargs: nullcontext(),
        )

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
        self.assertIn("opening segment", calls[0]["segment_position"])
        self.assertIn("middle segment", calls[1]["segment_position"])
        self.assertIn("final segment", calls[2]["segment_position"])
        self.assertIn("Covered concept 1.", calls[1]["continuity_context"])
        self.assertIn("First expert turn.", calls[1]["continuity_context"])
        self.assertIn("Covered concept 2.", calls[2]["continuity_context"])
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
                return SimpleNamespace(
                    dialogue="HOST: Hello.\nEXPERT: Welcome.",
                    coverage_notes="Introduces the main concept.",
                )

        fake_dspy = SimpleNamespace(
            Signature=FakeSignature,
            InputField=lambda: None,
            OutputField=lambda: None,
            LM=lambda *_args, **kwargs: kwargs,
            Predict=FakePredict,
            context=lambda **_kwargs: nullcontext(),
        )

        with patch.dict(sys.modules, {"dspy": fake_dspy}):
            result = scripts.generate_script(
                "groq", "test-model", "test-key", "Short source.", None
            )

        self.assertEqual(len(calls), 1)
        self.assertNotIn("one segment", calls[0]["guidelines"])
        self.assertIn("complete episode", calls[0]["segment_position"])
        self.assertIn(
            "No earlier podcast dialogue",
            calls[0]["continuity_context"],
        )
        self.assertEqual(result, "HOST: Hello.\nEXPERT: Welcome.\n")

    def test_preparation_removes_page_markers_and_bibliography(self):
        source_text = (
            "[Page 1]\n1 Introduction\nThe useful findings are here.\n\n"
            "[Page 9]\nReferences\nAuthor. A paper title. 2024."
        )

        prepared = scripts.prepare_source_text(source_text)

        self.assertNotIn("[Page", prepared)
        self.assertNotIn("References", prepared)
        self.assertNotIn("A paper title", prepared)
        self.assertIn("The useful findings are here.", prepared)

    def test_language_model_output_limit_is_provider_safe(self):
        self.assertEqual(scripts.MAX_SCRIPT_OUTPUT_TOKENS, 4096)


if __name__ == "__main__":
    unittest.main()
