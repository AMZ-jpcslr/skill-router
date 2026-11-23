"""Developer writing tasks and clause splitting; synthetic metadata modelled on real skills."""
import runpy
import unittest
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "plugins/skill-router/skills/skill-router-settings"
INDEX = runpy.run_path(str(CORE / "local_index.py"))
OPEN = INDEX["OPEN"]


def row(name, description):
    return {"name": name, "path": f"{name}/SKILL.md", "description": description}


ROWS = [
    row("commit", "Creates commits following conventional commit format and issue references. Trigger on any commit or git commit."),
    row("changelog-automation", "Automate changelog generation from commits, PRs, and releases following Keep a Changelog format."),
    row("wiki-changelog", "Generate structured changelogs from git history. Use when user asks to summarize commits."),
    row("pr-writer", "Create pull requests following engineering practices."),
    row("code-reviewer", "Elite code review expert specializing in modern AI-powered code"),
    row("receiving-code-review", "Code review requires technical evaluation, not emotional performance."),
    row("professional-proofreader", 'Use when a user asks to "proofread", "fix grammar", and to proofread a document file and save an updated version.'),
    row("photopea-embedded-editor", "Embed Photopea in web apps using photopea.js. Covers embedding, file I/O, scripting, exporting, layers, text, filters, and the full API."),
    row("draft-career-es", "エントリーシートの志望動機や自己PRを作成・添削する。"),
]
# Unrelated filler gives the term weights a realistic catalogue size.
TOPICS = ("kubernetes", "terraform", "spreadsheet", "calendar", "pdf", "slides", "audio", "maps", "invoice", "sql")
ROWS += [row(f"filler-{t}-{i}", f"Work with {t} projects: setup, analysis and reporting for {t} variant {i}.")
         for t in TOPICS for i in range(20)]


def names(prompt):
    return {r["name"] for _, r in INDEX["rank"](prompt, ROWS, [])}


class DevTaskRouting(unittest.TestCase):
    def test_clauses(self):
        clauses = OPEN["work_clauses"]
        self.assertEqual(clauses("変更を見て、コミットメッセージを書いて"), ["変更を見て、コミットメッセージを書いて"])
        self.assertEqual(clauses("READMEを直して、コミットはしないで"), ["readmeを直して"])
        self.assertEqual(clauses("Summarize notes.md in three bullets. Do not edit the file."), ["summarize notes.md in three bullets"])

    def test_selection(self):
        cases = [
            ("ステージ済みの変更を見て、コミットメッセージを書いて。", {"commit"}),
            ("このブランチのPR説明文を作成して。", {"pr-writer"}),
            ("src/memo_store.py をコードレビューして。", {"code-reviewer"}),
            ("Proofread notes-en.md. Fix grammar and spelling only. Do not edit the file.", {"professional-proofreader"}),
            ("Summarize notes-en.md in three bullet points. Do not edit the file.", set()),
            ("123 + 456 はいくつ？", set()),
        ]
        for prompt, expect in cases:
            with self.subTest(prompt=prompt):
                self.assertEqual(names(prompt), expect)

    def test_equal_family_members_are_both_offered(self):
        self.assertEqual(names("前回のタグからの変更をまとめてCHANGELOGを更新して。"), {"changelog-automation", "wiki-changelog"})


if __name__ == "__main__":
    unittest.main()
