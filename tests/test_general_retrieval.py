"""General retrieval: words learned from the catalogue, request form, files, shortlists."""
import runpy
import tempfile
import unittest
from pathlib import Path

CORE = Path(__file__).resolve().parents[1] / "plugins/skill-router/skills/skill-router-settings"
INDEX = runpy.run_path(str(CORE / "local_index.py"))
OPEN = INDEX["OPEN"]


def row(name, description, keywords=()):
    return {"name": name, "path": f"/s/{name}/SKILL.md", "description": description, "keywords": list(keywords)}


TOPICS = ("kubernetes", "terraform", "spreadsheet", "calendar", "pdf", "slides", "audio", "maps", "invoice", "sql")
FILLER = [row(f"filler-{t}-{i}", f"Work with {t} projects: setup, analysis and reporting for {t} variant {i}.")
          for t in TOPICS for i in range(20)]


def names(prompt, rows):
    return [r["name"] for _, r in INDEX["rank"](prompt, rows, [])]


def stages(prompt, rows):
    return {r["name"]: k for k, r in INDEX["rank"](prompt, rows, [])}


class LearnedVocabulary(unittest.TestCase):
    def test_japanese_request_reaches_an_english_only_skill(self):
        # Skills that say both "セキュリティ" and "security" (or "脆弱性" and "vulnerability") teach
        # the router that they are the same word; a skill described only in English is then
        # found from a Japanese request.
        taught = ([row(f"guard-{i}", f"Security hardening for service {i}.", [f"セキュリティ強化{i}"]) for i in range(3)]
                  + [row(f"scan-{i}", f"Vulnerability scanning for host {i}.", [f"脆弱性検査{i}"]) for i in range(3)])
        target = row("security-review", "Review code for security vulnerabilities before release.")
        rows = FILLER + taught + [target]
        counts = OPEN["catalog_counts"](OPEN["prepare"](r) for r in rows)
        self.assertIn("security", dict(counts[2].get("セキュリティ", ())))
        q = OPEN["Query"]("リリース前にセキュリティの脆弱性がないか確認して", "", counts)
        self.assertIn("security", q.translated)
        score, evidence, _ = OPEN["score_skill"](q, OPEN["prepare"](target), counts[0], counts[1], len(rows), target)
        self.assertTrue({"security", "vulnerability"} <= evidence)
        self.assertGreater(score, 5)
        # Without the bilingual evidence the same request shares no word with it.
        alone = OPEN["catalog_counts"](OPEN["prepare"](r) for r in FILLER + [target])
        q = OPEN["Query"]("リリース前にセキュリティの脆弱性がないか確認して", "", alone)
        self.assertEqual(OPEN["score_skill"](q, OPEN["prepare"](target), alone[0], alone[1], len(FILLER) + 1, target)[1], set())


class RequestForm(unittest.TestCase):
    def test_requests_by_form(self):
        is_request = OPEN["is_request"]
        for text in ("angularjsの古いコードを移行する手順を進めて", "テーマにレビュー欄を追加したい",
                     "refactor this component into smaller pieces.", "このバグ直してくれる?",
                     "help me speed it up."):
            self.assertTrue(is_request(text), text)
        for text in ("git rebase と merge の違いを教えて", "なんでこのテストは失敗してるの?",
                     "great, that works. thanks!", "what time is it in london right now?",
                     "次に学ぶならどっちがいいと思う?"):
            self.assertFalse(is_request(text), text)

    def test_file_names_give_only_their_format(self):
        self.assertEqual(OPEN["strip_references"]("notes-en.md を要約して").split(), ["を要約して"])
        self.assertEqual(OPEN["file_formats"]("経費.xlsx と src/a.py を見て"), frozenset({"xlsx", "py"}))
        md = row("agents-md", "Write AGENTS.md files for repositories.")
        self.assertEqual(names("Summarize notes-en.md in three bullet points.", FILLER + [md]), [])


class Shortlist(unittest.TestCase):
    def test_close_candidates_are_listed_for_the_agent(self):
        same = "Docker containerization: create Dockerfiles, images and compose files."
        rows = FILLER + [row("docker-expert", same), row("docker-pro", same)]
        got = stages("Write a Dockerfile for this app.", rows)
        self.assertEqual(set(got.values()), {"open-shortlist"})
        self.assertEqual(set(got), {"docker-expert", "docker-pro"})

    def test_clear_candidate_is_handed_alone(self):
        rows = FILLER + [row("mermaid-expert", "Create Mermaid diagrams: flowcharts, sequence diagrams, ERDs.")]
        self.assertEqual(stages("Draw this login flow as a Mermaid sequence diagram.", rows), {"mermaid-expert": "open-search"})


class FoldedDescription(unittest.TestCase):
    def test_description_continued_on_indented_lines(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "SKILL.md"
            p.write_text("---\nname: aws-serverless\ndescription: Build serverless\n  applications on AWS with Lambda.\nrisk: low\n---\nbody\n", encoding="utf-8")
            self.assertEqual(INDEX["metadata"](p), ("aws-serverless", "Build serverless applications on AWS with Lambda."))
            p.write_text('---\nname: q\ndescription: "Quoted and\n  folded."\n---\n', encoding="utf-8")
            self.assertEqual(INDEX["metadata"](p), ("q", "Quoted and folded."))


class BroadSkills(unittest.TestCase):
    def test_a_guide_listing_several_tasks_is_not_a_specialist(self):
        # A writing guide whose description lists PR descriptions and AI-sounding prose among
        # its uses is not penalised as a PR or AI-writing specialist on another writing request.
        guide = row("plain-writing", "Write and revise work documents in plain language: minutes, reports, "
                    "PR descriptions, emails. Also flags text that sounds AI-generated.")
        nc = OPEN["prepare"](guide)[1]
        self.assertEqual(nc, frozenset())
        named = OPEN["prepare"](row("changelog-automation", "Automate changelog generation from commits, PRs, and releases."))[1]
        self.assertEqual(named, frozenset({"changelog"}))

    def test_a_word_inside_a_compound_name_is_not_decisive(self):
        # "LINE" (the messenger) is the word "line" of subject-line-psychologist, but a word
        # inside a compound name does not, alone, say what the skill is about.
        rows = FILLER + [row("subject-line-psychologist", "Write email subject lines that get opened.")]
        self.assertEqual(names("友達から来たLINEへの返信を考えて。", rows), [])
        rows = FILLER + [row("mermaid-expert", "Create Mermaid diagrams.")]
        self.assertEqual(names("このフローをmermaidで描いて。", rows), ["mermaid-expert"])

    def test_space_between_latin_and_japanese_does_not_hide_a_task(self):
        # Japanese typesetting often writes "AI っぽい"; it names the same task as "AIっぽい".
        self.assertIn("ai-writing", OPEN["features"]("AI っぽい文章を直す")[2])
        self.assertIn("ai-writing", OPEN["features"]("AIっぽい文章を直す")[2])


if __name__ == "__main__":
    unittest.main()
