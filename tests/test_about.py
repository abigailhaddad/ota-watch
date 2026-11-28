"""2026-11-28: the about page is one sentence plus links to the files that hold the rules. Models, dollar amounts,
counts and prompt text live in config/ota-watch.yaml and the linked code and must not be copied here."""
import re
from pathlib import Path

from ota_watch import site
from ota_watch.settings import CONFIG

ROOT = Path(__file__).resolve().parent.parent


def _main():
    html = site.about_page()
    return html[html.index("<main>"):html.index("</main>")]


def test_about_page_hard_codes_nothing_that_drifts():
    main = _main()
    text = re.sub(r"<[^>]+>", " ", main)
    assert "claude-" not in main.lower()
    assert not re.search(r"\$\s?\d", text)
    assert not re.search(r"\d", text), "no numbers (counts, thresholds) on the about page"
    for stage in CONFIG.values():
        assert str(stage["model"]) not in main
        for key in ("prompt", "voice"):
            for line in str(stage.get(key, "")).splitlines():
                if len(line.strip()) > 40:
                    assert line.strip()[:40] not in text


def test_about_page_links_files_that_exist():
    paths = set(re.findall(re.escape(site.PROJECT_URL) + r"/blob/main/([^\"#]+)", _main()))
    assert paths == {"ota_watch/digest.py", "config/ota-watch.yaml", "ota_watch/opening.py"}
    for p in paths:
        assert (ROOT / p).exists(), p
    assert site.DATASET_URL in _main()


def test_every_issue_ends_with_the_how_this_is_made_line():
    import json
    issues = sorted((ROOT / "data" / "issues").glob("*.json"))
    assert issues
    for f in issues:
        issue = json.loads(f.read_text())
        html = site.issue_content(issue)
        last = html.rstrip().splitlines()[-1]
        link = (f'<a href="{site.SITE_URL}/about">How this is made</a> · '
                f'<a href="https://github.com/abigailhaddad/ota-watch">Code</a>')
        claude = bool(issue["picks"] or site.kept_text(issue))
        assert last == f'<p class="note how">{"Written with Claude. " if claude else ""}{link}</p>', f.name
    assert site.how_line(False) == ('<p class="note how"><a href="https://ota-watch.abigailhaddad.com/about">How this is made</a>'
                                    ' · <a href="https://github.com/abigailhaddad/ota-watch">Code</a></p>')
