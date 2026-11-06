# Handoff: ota-watch

## What this is
Every federal Other Transaction (OT) record in FPDS, a weekly site that picks out a few new awards, and the full record set on Hugging Face.

- Code: https://github.com/abigailhaddad/ota-watch (README: how it works and the caveats)
- Data: https://huggingface.co/datasets/abigailhaddad/ota-watch (the dataset card has the columns and known limits)
- Site: https://ota-watch.abigailhaddad.com

## What runs on its own
`.github/workflows/weekly.yml` runs daily (the schedule is at the top of the file). It reads new records from FPDS, builds the weekly issue on Mondays (the only step that calls Claude), commits the data to `main`, deploys the site to Cloudflare Pages and updates the Hugging Face dataset. `tests.yml` runs the tests.

## Secrets and variables
Repository secrets: `HF_TOKEN` (write access to the dataset), `ANTHROPIC_API_KEY` (Monday issue), `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` (site deploy). Variables, both optional: `ANTHROPIC_WORKSPACE_ID` and `CLOUDFLARE_PROJECT` (default `ota-watch`). The weekly workflow's header comment lists the same names.

## If you take it over
- Owner names are in `ota_watch/config.py`; each can be set with an environment variable (`OTA_GITHUB_REPO`, `OTA_HF_REPO`, `OTA_SITE_URL`). The README also links the current owner's site, dataset and issues page. To find everything: `grep -rn abigailhaddad --exclude-dir=.git .`
- Create: a Hugging Face dataset and token, a Cloudflare Pages project (default name `ota-watch`) with your domain, and the secrets above.
- A new empty dataset: the daily publish is written to create it. That path is tested only against fakes. It then holds only the last 400 days (`LOOKBACK_DAYS` in `ota_watch/collect.py`). For older records, run the backfill described in the README (step 3).
- The weekly issue needs an earlier issue to measure from. For the very first one, see the docstring at the top of `ota_watch/digest.py`.

## When it breaks
- Failed runs show in the Actions tab. The workflow doesn't open issues or send alerts, so turn on GitHub notifications for the repo.
- Rerun by hand from the Actions tab (Run workflow). Tick "Build this week's issue" to build one on a day that isn't Monday.
- A failed step doesn't undo the data commit. Rerunning redoes the deploy and the publish.
- The "Commit data" step pushes straight to `main`, so branch protection would stop it.

## What it costs
Only the Monday issue calls an LLM (`ota_watch/digest.py`; the model defaults to `claude-opus-5-5`, set `OTA_MODEL` to change it). Each issue file in `data/issues/` records its input and output tokens under `usage`.
