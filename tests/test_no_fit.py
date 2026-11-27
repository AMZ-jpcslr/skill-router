"""No-fit record: the Stop hook keeps, on this computer only, the words of work requests no
skill was used for; skill-suggest turns that record into public skills to look at."""
from datetime import date, timedelta
import json
import os
from pathlib import Path
import runpy
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/skill-omakase"
ROUTER = PLUGIN / "skills/skill-omakase-settings/router.py"
REQUEST = "この契約書のリスクのある条項をレビューして"


class NoFitRecord(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home, self.project = base / "home", base / "project"
        (self.project / ".git").mkdir(parents=True)
        self.state = self.home / ".skill-omakase"
        self.state.mkdir(parents=True)
        (self.state / "settings.json").write_text(json.dumps(
            {"all_projects": False, "folderless": False, "projects": [str(self.project.resolve())]}), encoding="utf-8")
        skill = self.home / ".agents/skills/apology-letters/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("---\nname: apology-letters\ndescription: Formal apology letter templates for customers.\n---\n", encoding="utf-8")
        self.env = {k: v for k, v in os.environ.items() if k not in ("PLUGIN_ROOT", "SKILL_OMAKASE_HOST", "SKILL_OMAKASE_STATE_DIR")}
        self.env.update(HOME=str(self.home), USERPROFILE=str(self.home), CLAUDE_PLUGIN_ROOT=str(PLUGIN),
                        CLAUDE_CONFIG_DIR=str(self.home / ".claude"), CODEX_HOME=str(self.home / ".codex"))
        subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", str(ROUTER), "index", "refresh"],
                       env=self.env, check=True, capture_output=True, timeout=60)
        self.n = 0

    def tearDown(self):
        self.tmp.cleanup()

    def transcript(self, records):
        self.n += 1
        path = self.home / f"t{self.n}.jsonl"
        path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records), encoding="utf-8")
        return path

    def claude_turn(self, prompt, tools=()):
        recs = [{"type": "user", "message": {"role": "user", "content": "前の依頼を書いて"}},
                {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Read",
                 "input": {"file_path": "/x/.agents/skills/apology-letters/SKILL.md"}}]}},
                {"type": "user", "message": {"role": "user", "content": prompt}}]
        for name, inp in tools:
            recs.append({"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name, "input": inp}]}})
            recs.append({"type": "user", "message": {"content": [{"type": "tool_result", "content": "ok"}]}})
        return self.transcript(recs)

    def stop(self, path, session="s1", codex=False, cwd=None, **env):
        e = dict(self.env, **env)
        if codex:
            e["PLUGIN_ROOT"] = str(PLUGIN)
        event = json.dumps({"hook_event_name": "Stop", "session_id": session, "transcript_path": str(path),
                            "cwd": str(cwd or self.project), "stop_hook_active": False})
        r = subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", str(PLUGIN / "hooks/run_hook.py"), "stop"],
                           input=event.encode(), capture_output=True, env=e, cwd=self.project, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr.decode(errors="replace"))
        return json.loads(r.stdout)

    def log(self):
        p = self.state / "no-fit.jsonl"
        return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []

    def test_request_without_a_skill_is_recorded_as_words_only(self):
        self.assertEqual(self.stop(self.claude_turn(REQUEST, [("Bash", {"command": "ls"})])), {})
        log = self.log()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0]["h"], "claude-code")
        self.assertTrue({"契約書", "条項"} <= set(log[0]["w"]), log)
        raw = (self.state / "no-fit.jsonl").read_text(encoding="utf-8")
        self.assertNotIn(REQUEST, raw)
        self.assertNotIn("レビューして", raw)

    def test_same_turn_is_recorded_once(self):
        t = self.claude_turn(REQUEST)
        self.stop(t)
        self.stop(t)
        self.assertEqual(len(self.log()), 1)

    def test_turn_that_used_a_skill_is_not_recorded(self):
        self.stop(self.claude_turn(REQUEST, [("Skill", {"skill": "plugin:contract-review"})]))
        self.stop(self.claude_turn(REQUEST + "。", [("Read", {"file_path": "/h/.agents/skills/contract-review/SKILL.md"})]), session="s2")
        self.stop(self.claude_turn(REQUEST + "！", [("Bash", {"command": "cat ~/.codex/skills/contract-review/SKILL.md"})]), session="s3")
        self.assertEqual(self.log(), [])

    def test_not_recorded_without_work_or_when_named_or_off(self):
        self.stop(self.claude_turn("ありがとう"))
        self.stop(self.claude_turn("$contract-review " + REQUEST), session="s2")
        self.stop(self.claude_turn("<command-name>/contract-review</command-name>"), session="s3")
        # The router is off for this folder.
        other = Path(self.tmp.name) / "other"
        (other / ".git").mkdir(parents=True)
        self.stop(self.claude_turn(REQUEST), session="s4", cwd=other)
        self.assertEqual(self.log(), [])
        subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", str(ROUTER), "record-gaps", "off"],
                       env=self.env, check=True, capture_output=True, timeout=30)
        self.stop(self.claude_turn(REQUEST), session="s5")
        self.assertEqual(self.log(), [])

    def test_turning_off_removes_the_record(self):
        self.stop(self.claude_turn(REQUEST))
        self.assertTrue(self.log())
        out = subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", str(ROUTER), "record-gaps", "off"],
                             env=self.env, check=True, capture_output=True, timeout=30)
        self.assertFalse(json.loads(out.stdout)["record_gaps"])
        self.assertFalse((self.state / "no-fit.jsonl").exists())
        # Routing off keeps the choice.
        subprocess.run([sys.executable, "-I", "-B", "-X", "utf8", str(ROUTER), "off"], env=self.env, check=True, capture_output=True, timeout=30)
        self.assertFalse(json.loads((self.state / "settings.json").read_text(encoding="utf-8"))["record_gaps"])

    def test_codex_transcript(self):
        recs = [{"type": "event_msg", "payload": {"type": "user_message", "message": REQUEST}},
                {"type": "response_item", "payload": {"type": "function_call", "name": "shell", "arguments": "{\"command\":[\"ls\"]}"}}]
        self.stop(self.transcript(recs), codex=True)
        log = self.log()
        self.assertEqual(log[0]["h"], "codex")
        recs.append({"type": "response_item", "payload": {"type": "function_call", "name": "shell",
                     "arguments": "{\"command\":[\"cat\",\"/h/.codex/skills/contract-review/SKILL.md\"]}"}})
        self.stop(self.transcript(recs), session="s2", codex=True)
        self.assertEqual(len(self.log()), 1)

    def test_recurring_work_gets_one_notice_a_week(self):
        today = date.today()
        with (self.state / "no-fit.jsonl").open("w", encoding="utf-8") as f:
            for d in (5, 3):
                f.write(json.dumps({"d": (today - timedelta(days=d)).isoformat(), "h": "codex", "w": ["契約書", "条項", "リスク"]}) + "\n")
            f.write(json.dumps({"d": (today - timedelta(days=4)).isoformat(), "h": "codex", "w": ["会議", "招待", "メール"]}) + "\n")
        out = self.stop(self.claude_turn(REQUEST))
        self.assertIn("systemMessage", out)
        self.assertIn("3日", out["systemMessage"])
        self.assertIn("契約書", out["systemMessage"])
        self.assertNotIn("会議", out["systemMessage"])
        self.assertEqual(self.stop(self.claude_turn("別の契約書の条項のリスクを確認して"), session="s2"), {})


class NoFitThemes(unittest.TestCase):
    def setUp(self):
        self.G = runpy.run_path(str(PLUGIN / "skills/skill-omakase-settings/gap_log.py"))

    def test_kinds_of_work_are_grouped_by_shared_words(self):
        e = lambda d, *w: {"d": d, "h": "codex", "w": list(w)}
        themes = self.G["themes"]([e("2026-11-01", "契約書", "条項", "リスク"), e("2026-11-02", "契約書", "条項", "nda"),
                                   e("2026-11-03", "会議", "招待", "メール"), e("2026-11-03", "会議", "辞退", "メール"),
                                   e("2026-11-04", "文字起", "決定事項", "担当者")])
        self.assertEqual(len(themes), 3)
        self.assertEqual(themes[0]["days"], 2)
        self.assertIn("契約書", themes[0]["terms"])
        self.assertEqual({t["requests"] for t in themes}, {2, 1})


class NoFitReport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name)
        self.env = {"SKILL_OMAKASE_STATE_DIR": str(self.home / "state"), "CLAUDE_CONFIG_DIR": str(self.home / ".claude"),
                    "CODEX_HOME": str(self.home / ".codex")}
        self.saved = {k: os.environ.get(k) for k in self.env}
        os.environ.update(self.env)
        (self.home / "state").mkdir()

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.tmp.cleanup()

    def test_report_lists_recorded_work_with_public_shortlist(self):
        today = date.today()
        with (self.home / "state/no-fit.jsonl").open("w", encoding="utf-8") as f:
            for d in (1, 2):
                f.write(json.dumps({"d": (today - timedelta(days=d)).isoformat(), "h": "claude-code",
                                    "w": ["議事録", "決定事項", "担当者"]}, ensure_ascii=False) + "\n")
            f.write(json.dumps({"d": (today - timedelta(days=400)).isoformat(), "h": "codex", "w": ["古い"]}) + "\n")
        out = runpy.run_path(str(PLUGIN / "skills/skill-suggest/suggest.py"))["analyse"]()
        nf = out["no_fit"]
        self.assertEqual(nf["recorded"], 2)
        top = nf["themes"][0]
        self.assertEqual(top["days"], 2)
        self.assertIn("meeting-notes", [p["skill"] for p in top["public"]])
        self.assertIn("command", nf["elsewhere"])
        self.assertNotIn("古い", json.dumps(nf, ensure_ascii=False))


if __name__ == "__main__":
    unittest.main()
