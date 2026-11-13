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

os.environ["BOOKS_NO_ENV_FILE"] = "1"  # never let a local .env steer the tests

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



class ClaudeCodeTests(unittest.TestCase):
    def reply(self, **fields):
        import json as _json

        return mock.Mock(stdout=_json.dumps({"type": "result", "is_error": False, **fields}),
                         stderr="", returncode=0)

    def test_the_provider_is_recognised_and_ignores_bedrock_model_names(self):
        with clean_env(BOOKS_PROVIDER="claude-code"):
            self.assertEqual(judge.provider(), "claude-code")
            self.assertEqual(judge.resolve_model("eu.anthropic.claude-opus-5-5"), judge.DEFAULT_MODEL)
            self.assertEqual(judge.resolve_model("sonnet"), "sonnet")

    def test_a_request_becomes_one_run_of_the_claude_program(self):
        import claude_code

        schema = {"type": "object"}
        with mock.patch.object(claude_code, "executable", return_value="claude"), mock.patch.object(
            claude_code.subprocess, "run", return_value=self.reply(structured_output={"works": []})
        ) as run:
            response = claude_code.ClaudeCodeClient().messages.create(
                model="claude-opus-5-5",
                max_tokens=100,
                system=[{"type": "text", "text": "Be brief."}],
                output_config={"effort": "medium", "format": {"type": "json_schema", "schema": schema}},
                messages=[{"role": "user", "content": "the whole stretch of text"}],
            )
        command = run.call_args.args[0]
        self.assertEqual(command[:4], ["claude", "-p", "--model", "claude-opus-5-5"])
        self.assertEqual(command[command.index("--system-prompt") + 1], "Be brief.")
        self.assertEqual(command[command.index("--json-schema") + 1], '{"type": "object"}')
        self.assertEqual(command[command.index("--tools") + 1], "")  # it may only answer
        self.assertEqual(run.call_args.kwargs["input"], "the whole stretch of text")
        self.assertEqual(response.content[0].text, '{"works": []}')
        self.assertEqual(response.stop_reason, "end_turn")

    def test_an_answer_wrapped_in_prose_is_still_read(self):
        import claude_code

        with mock.patch.object(claude_code, "executable", return_value="claude"), mock.patch.object(
            claude_code.subprocess, "run",
            return_value=self.reply(result='Here you go:\n```json\n{"works": []}\n```'),
        ):
            with claude_code.ClaudeCodeClient().messages.stream(
                model="m", system="s", messages=[{"role": "user", "content": "t"}]
            ) as stream:
                self.assertEqual(stream.get_final_message().content[0].text, '{"works": []}')

    def test_a_surrounding_claude_code_session_is_not_inherited(self):
        import claude_code

        inside = {"CLAUDECODE": "1", "CLAUDE_CODE_SESSION_ID": "x", "ANTHROPIC_BASE_URL": "u", "PATH": "p"}
        self.assertEqual(claude_code._own_environment(inside), {"PATH": "p"})
        own = {"ANTHROPIC_BASE_URL": "u", "PATH": "p"}  # set by the person, not by a session
        self.assertEqual(claude_code._own_environment(own), own)
        # an API key would switch the program away from the subscription login
        self.assertEqual(claude_code._own_environment({"ANTHROPIC_API_KEY": "k", "PATH": "p"}), {"PATH": "p"})

    def test_tokens_are_counted_across_requests(self):
        import claude_code

        reply = self.reply(structured_output={"works": []},
                           usage={"input_tokens": 100, "cache_read_input_tokens": 900, "output_tokens": 40})
        before = claude_code.usage()
        with mock.patch.object(claude_code, "executable", return_value="claude"), mock.patch.object(
            claude_code.subprocess, "run", return_value=reply
        ):
            for _ in range(2):
                claude_code.ask("m", "s", [{"role": "user", "content": "t"}])
        after = claude_code.usage()
        self.assertEqual(
            {k: after[k] - before[k] for k in after},
            {"calls": 2, "input_tokens": 2000, "output_tokens": 80},
        )

    def test_an_expired_login_says_how_to_fix_it(self):
        import claude_code

        failed = self.reply(is_error=True, result="Failed to authenticate: OAuth session expired")
        with mock.patch.object(claude_code, "executable", return_value="claude"), mock.patch.object(
            claude_code.subprocess, "run", return_value=failed
        ):
            with self.assertRaisesRegex(RuntimeError, "/login"):
                claude_code.ask("m", "s", [{"role": "user", "content": "t"}])

    def test_without_the_program_the_judge_reports_why(self):
        import claude_code

        with clean_env(BOOKS_PROVIDER="claude-code"), mock.patch.object(
            claude_code, "executable", return_value=None
        ):
            judge._client, judge._disabled_reason = None, None
            status = judge.judge_status()
            self.assertFalse(status["available"])
            self.assertIn("claude", status["reason"])
            judge._client, judge._disabled_reason = None, None

if __name__ == "__main__":
    unittest.main()
