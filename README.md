# OTA Watch

**https://ota-watch.abigailhaddad.com**: every federal Other Transaction (OT) award record newly public in
FPDS, listed weekly, with the most remarkable few picked out and summarized by Claude.

OTs are agreements made outside the Federal Acquisition Regulation, mostly by the Defense Department for
prototypes and follow-on production. They are reported to FPDS (the federal procurement database) but not to
USAspending. FPDS's public ATOM feed is the only source used here.

## How it works

1. **Collect** (`ota_watch/collect.py`): every Monday, read every OT document signed in the last 400 days
   from FPDS and add the ones not seen before to `data/documents.csv` with `first_seen` = that day. "Newly
   public" is decided by what FPDS showed before, not by signing date: Defense OT records become public about
   90 days after signing, and some are entered months later still (one signed 2026-01-20 was entered
   2026-10-19). Records already public when the project started are marked `first_seen = baseline`.
2. **Digest** (`ota_watch/digest.py`): the week's records go to Claude (`claude-opus-5-5`), which picks
   up to five and writes a headline and a short explanation for each, quoting the record's description.
   Code then checks every pick: the quote must be in the record (ignoring case and whitespace), and every
   dollar figure must be one of the pick's records' obligations or their sum; a pick that fails is dropped
   (kept in the issue file under `dropped`). All other numbers on the page (counts, totals, fact lines) are
   computed from the records, not written by the model. One issue a week at most.
3. **Site** (`ota_watch/site.py`): static pages from `data/issues/*.json`, deployed to Cloudflare Pages.
   No JavaScript, no cookies, no trackers.

## Caveats

* **Dollars are obligations on each action.** FPDS's "total" and "base and all options" values on OT records
  are unreliable (one DoD OT reads $100M, $281M and $351.6M in different fields) and are never shown; they
  are kept in `documents.csv` as `*_raw` columns for reference.
* **The 90-day delay** means most of each week's records were signed about three months earlier.
* **A record entered more than 400 days after signing would be missed.**
* Claude can misread a record. Each record links to FPDS's public feed (XML).

## Run it

```sh
uv sync
uv run pytest
uv run python -m ota_watch.collect                       # this week's FPDS read
ANTHROPIC_API_KEY=... uv run python -m ota_watch.digest  # this week's issue
uv run python -m ota_watch.site --out site/dist
```

Spotted a wrong pick, quote or figure? [Open an issue](https://github.com/abigailhaddad/ota-watch/issues/new).

Data: FPDS records are U.S. government data. Code: MIT.
