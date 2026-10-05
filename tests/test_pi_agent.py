"""Check Pi event handling without making model calls or desktop changes."""

import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import pi_agent  # noqa: E402


@patch.object(pi_agent.shutil, "copyfile")
class PiAgentTest(unittest.TestCase):
    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch.object(pi_agent, "layout_summary", return_value="")
    @patch("pathlib.Path.exists", return_value=True)
    @patch.object(pi_agent.subprocess, "Popen")
    def test_collects_tool_actions_and_reply(self, popen, _exists, _layout, _copyfile):
        events = [
            {"type": "tool_execution_end", "toolName": "desktop_action", "isError": False,
             "result": {"details": {"action": "search_files", "files": ["/home/voiceos/notes.txt"]}}},
            {"type": "message_end", "message": {"role": "assistant", "content": [
                {"type": "text", "text": "I found your notes."}]}}
        ]
        proc = SimpleNamespace(
            communicate=lambda timeout=None: ("\n".join(map(json.dumps, events)), ""),
            returncode=0, poll=lambda: 0, kill=lambda: None)
        popen.return_value = proc
        result = pi_agent.interpret_voice_request("find my notes")
        self.assertEqual(result["message"], "I found your notes.")
        self.assertEqual(result["files"], ["/home/voiceos/notes.txt"])
        args = popen.call_args.args[0]
        self.assertIn("--no-builtin-tools", args)
        self.assertIn("desktop_action", args)
        self.assertEqual(args[-1], "find my notes")

    @patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"})
    @patch.object(pi_agent, "layout_summary", return_value="")
    @patch("pathlib.Path.exists", return_value=True)
    @patch.object(pi_agent.subprocess, "Popen")
    def test_history_is_added_to_prompt(self, popen, _exists, _layout, _copyfile):
        proc = SimpleNamespace(
            communicate=lambda timeout=None: ("", ""),
            returncode=0, poll=lambda: 0, kill=lambda: None)
        popen.return_value = proc
        pi_agent.interpret_voice_request(
            "now close it",
            history=[{"text": "open terminal", "message": "Opened a terminal."}])
        prompt = popen.call_args.args[0][-1]
        self.assertIn("Earlier request", prompt)
        self.assertIn("open terminal", prompt)
        self.assertTrue(prompt.endswith("now close it"))

    @patch.dict(os.environ, {}, clear=True)
    def test_key_is_required(self, _copyfile):
        with self.assertRaisesRegex(RuntimeError, "OPENAI_API_KEY"):
            pi_agent.interpret_voice_request("open terminal")


if __name__ == "__main__":
    unittest.main()
