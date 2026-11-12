"""Offline tests for choosing between the Claude API and Amazon Bedrock."""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import judge  # noqa: E402
import reader  # noqa: E402

BEDROCK = {"BOOKS_PROVIDER": "bedrock", "AWS_REGION": "eu-central-1", "AWS_PROFILE": "my-profile"}
VERDICT = {
    "source_year": None,
    "verdicts": [
        {"index": 0, "is_reference": True, "kind": "book", "canonical_title": "Emma",
         "author": "Jane Austen", "manner": "read", "reason": "ok"}
    ],
}


def clean_env(**values):
    """The environment with every provider setting removed, then `values` applied."""
    names = ["BOOKS_PROVIDER", "BOOKS_BEDROCK_API", "BOOKS_JUDGE_MODEL", "BOOKS_READER_MODEL",
             "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_PROFILE", "BOOKS_JUDGE"]
    env = {k: v for k, v in os.environ.items() if k not in names}
    env.update(values)
    return mock.patch.dict(os.environ, env, clear=True)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        for name in ("_client", "_disabled_reason"):
            patcher = mock.patch.object(judge, name, None)
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_default_is_the_claude_api_with_server_side_fallback(self):
        with clean_env():
            self.assertEqual(judge.provider(), "anthropic")
            self.assertEqual(judge.judge_model(), "claude-opus-5-5")
            client = SimpleNamespace(beta=SimpleNamespace(messages="beta-api"), messages="plain-api")
            api, extra = judge.message_api(client)
            self.assertEqual(api, "beta-api")
            self.assertEqual(extra["fallbacks"], "default")

    def test_bedrock_uses_bedrock_model_names_and_no_fallback_parameter(self):
        with clean_env(**BEDROCK):
            self.assertEqual(judge.provider(), "bedrock")
            self.assertEqual(judge.judge_model(), "anthropic.claude-opus-5-5")
            self.assertEqual(reader.reader_model(), "anthropic.claude-opus-5-5")
            client = SimpleNamespace(beta=SimpleNamespace(messages="beta-api"), messages="plain-api")
            self.assertEqual(judge.message_api(client), ("plain-api", {}))

    def test_a_configured_model_is_used_exactly_as_written(self):
        with clean_env(**BEDROCK, BOOKS_JUDGE_MODEL="eu.anthropic.claude-opus-4-6-v1"):
            self.assertEqual(judge.judge_model(), "eu.anthropic.claude-opus-4-6-v1")

    def test_bedrock_client_gets_region_and_profile_but_no_keys(self):
        fake = SimpleNamespace(
            AnthropicBedrockMantle=mock.Mock(return_value="mantle-client"),
            AnthropicBedrock=mock.Mock(return_value="runtime-client"),
        )
        with clean_env(**BEDROCK), mock.patch.dict(sys.modules, {"anthropic": fake}):
            self.assertEqual(judge._get_client(), "mantle-client")
            fake.AnthropicBedrockMantle.assert_called_once_with(
                aws_region="eu-central-1", aws_profile="my-profile"
            )
            status = judge.judge_status()
            self.assertEqual((status["available"], status["provider"]), (True, "bedrock"))

    def test_older_accounts_can_use_the_invoke_model_endpoint(self):
        fake = SimpleNamespace(
            AnthropicBedrockMantle=mock.Mock(return_value="mantle-client"),
            AnthropicBedrock=mock.Mock(return_value="runtime-client"),
        )
        with clean_env(**BEDROCK, BOOKS_BEDROCK_API="runtime"), \
                mock.patch.dict(sys.modules, {"anthropic": fake}):
            self.assertEqual(judge._get_client(), "runtime-client")

    def test_bedrock_without_a_region_is_reported_not_guessed(self):
        with clean_env(BOOKS_PROVIDER="bedrock"):
            self.assertIsNone(judge._get_client())
            self.assertIn("AWS_REGION", judge.judge_status()["reason"])

    def test_judging_through_bedrock_sends_no_beta_arguments(self):
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text=json.dumps(VERDICT))],
            )

        client = SimpleNamespace(messages=SimpleNamespace(create=create))
        with clean_env(**BEDROCK):
            result = judge.judge_mentions("A Book", "An Author",
                                          [{"title": "Emma", "context": "reading Emma"}], client=client)
        self.assertEqual(result["verdicts"][0]["author"], "Jane Austen")
        self.assertEqual(calls[0]["model"], "anthropic.claude-opus-5-5")
        self.assertNotIn("betas", calls[0])
        self.assertNotIn("fallbacks", calls[0])


class EnvFileTests(unittest.TestCase):
    def test_env_file_fills_gaps_but_never_overrides(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text(
                "# a comment\nBOOKS_PROVIDER=bedrock\nAWS_REGION = \"eu-west-1\"\nnot a setting\n",
                encoding="utf-8",
            )
            with clean_env(AWS_REGION="eu-central-1"):
                judge.load_env_file(str(path))
                self.assertEqual(os.environ["BOOKS_PROVIDER"], "bedrock")
                self.assertEqual(os.environ["AWS_REGION"], "eu-central-1")

    def test_missing_env_file_is_fine(self):
        judge.load_env_file("no-such-file.env")


if __name__ == "__main__":
    unittest.main()
