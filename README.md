# OTA Watch

**https://ota-watch.abigailhaddad.com**: every federal Other Transaction (OT) award record newly public in
FPDS, listed weekly, with the most remarkable few picked out and summarized by Claude.

**Every OT record in FPDS**, not just the recent ones the site uses, is on Hugging Face:
[abigailhaddad/ota-watch](https://huggingface.co/datasets/abigailhaddad/ota-watch), as Parquet and CSV,
updated daily. Read its card before using it: it covers who the vendors are, money on OT IDVs, which agencies
are in the data, and how the mix changes over time, with every count and dollar figure computed from the data
each time it's built.

OTs are agreements made outside the Federal Acquisition Regulation for prototypes and follow-on production.
They are reported to FPDS (the federal procurement database) but not to USAspending. FPDS's public ATOM feed
is the only source used here.

## How it works

1. **Collect** (`ota_watch/collect.py`): every day, read every OT document signed in the last
   `LOOKBACK_DAYS` from FPDS and add the ones not seen before to `data/documents.csv` with `first_seen` = the
   time of that run (UTC). "Newly public" is decided by what FPDS showed before, not by signing date: Defense
   OT records become public in FPDS months after signing, and some are entered later still. Records already
   public when the project started are marked `first_seen = baseline` (on Hugging Face, so are records the
   collection never saw because they were signed too long ago).
2. **Digest** (`ota_watch/digest.py`), Mondays only: the week's records go to Claude (`MODEL`), which picks
   up to `MAX_PICKS` and writes a headline and a short explanation for each, quoting the record's
   description. Code then checks every pick: the quote must be in the record (ignoring case and whitespace),
   and every dollar figure must be one of the pick's records' obligations or their sum; a pick that fails is
   dropped (kept in the issue file under `dropped`). All other numbers on the page (counts, totals, fact
   lines) are computed from the records, not written by the model. One issue a week at most.
3. **Hugging Face** (`ota_watch/hf.py`): the full history (a backfill of every OT document FPDS has) with
   `data/documents.csv` laid over it each day. Documents signed before the collection window aren't re-read
   daily; rerun `python -m ota_watch.hf backfill DIR` and `publish --history DIR` to refresh them.
4. **Site** (`ota_watch/site.py`): static pages from `data/issues/*.json`, deployed to Cloudflare Pages.
   No JavaScript, no cookies, no trackers.

## Caveats

* **Dollars are obligations on each action.** FPDS's "total" and "base and all options" values on OT records
  are unreliable and are never shown; they are kept as `*_raw` columns for reference.
* **The Defense delay** means most of each week's Defense records were signed months earlier. The dataset
  card gives the delay measured from the data.
* **A record entered in FPDS after the collection window has passed its signing date is missed by the
  weekly site.** It reaches the Hugging Face dataset only when the full backfill is rerun.
* Claude can misread a record. Each record links to FPDS's public feed (XML).

## Run it

```sh
uv sync
uv run pytest
uv run python -m ota_watch.collect                       # today's FPDS read
ANTHROPIC_API_KEY=... uv run python -m ota_watch.digest  # this week's issue
uv run python -m ota_watch.site --out site/dist
HF_TOKEN=... uv run python -m ota_watch.hf publish       # update the Hugging Face dataset
```

Spotted a wrong pick, quote or figure? [Open an issue](https://github.com/abigailhaddad/ota-watch/issues/new).

Data: FPDS records are U.S. government data. Code: MIT.
