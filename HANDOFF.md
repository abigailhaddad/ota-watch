# OTA Watch: Operator Guide

For whoever runs this next. You don't need to be a developer for most of it.

## What it does

One GitHub Action (`.github/workflows/weekly.yml`) runs every day at 12:00 UTC. Each run:

1. Runs the tests. If they fail, nothing else happens.
2. Reads FPDS for Other Transaction documents signed in the last 400 days and adds new ones to `data/documents.csv`.
3. On Mondays only, builds that week's issue (`data/issues/<date>.json`). This is the one step that calls Claude. It picks a few records and writes them up. Code then checks every quote and dollar figure and drops any pick that fails.
4. Commits the data changes to `main`.
5. Builds the site and deploys it to Cloudflare Pages.
6. Updates the Hugging Face dataset with every OT record FPDS has.

You can also run it by hand from the Actions tab and tick "Build this week's issue" to get an issue on a day that isn't Monday. The code won't build a second issue in the same Monday–Sunday week.

## Where things live

| What | Where |
|---|---|
| Code | This repo (`ota_watch/`) |
| Recent records (the site's table) | `data/documents.csv` |
| Weekly issues | `data/issues/*.json` |
| The full dataset | Hugging Face (see `HF_REPO_ID` in `ota_watch/config.py`) |
| The site | Cloudflare Pages, built from `site/dist` (not committed) |
| Run history | The repo's Actions tab |

## Secrets and variables

Set these under Settings > Secrets and variables > Actions. The workflow reads them by these exact names.

| Name | Kind | Used for |
|---|---|---|
| `ANTHROPIC_API_KEY` | secret | The Monday issue. Only that step calls Claude. |
| `HF_TOKEN` | secret | Writing the Hugging Face dataset. Needs write access to it. |
| `CLOUDFLARE_API_TOKEN` | secret | Deploying the site to Cloudflare Pages. |
| `CLOUDFLARE_ACCOUNT_ID` | secret | Same deploy. |
| `ANTHROPIC_WORKSPACE_ID` | variable, optional | Sent as a header on Claude calls if set. |

The model is `claude-opus-5-5` unless the environment variable `OTA_MODEL` says otherwise (`ota_watch/digest.py`).

## Moving it to a new owner

All names live in `ota_watch/config.py`. Each can be set with an environment variable, or change the default in the file.

| Setting | Variable | Current default |
|---|---|---|
| GitHub repo | `OTA_GITHUB_REPO` | `abigailhaddad/ota-watch` |
| Hugging Face dataset | `OTA_HF_REPO` | `abigailhaddad/ota-watch` |
| Site address | `OTA_SITE_URL` | `https://ota-watch.abigailhaddad.com` |

The workflow can't read that file, so these places still need editing by hand:

- `.github/workflows/weekly.yml`: the Cloudflare Pages project name, `--project-name=ota-watch`, on the deploy step. Also the comments at the top, which mention the old site address and dataset.
- `README.md`: the site address in the first line, the dataset link, and the "Open an issue" link at the bottom.
- The Cloudflare side: the Pages project itself and its custom domain are set up in Cloudflare, not in this repo. I couldn't see them from here.
- The Hugging Face dataset under the new owner needs its table in place before the daily publish works. Publish first downloads the current table from the dataset, and only afterwards creates the repo if it's missing, so on a brand-new dataset the daily run fails. Do the first publish by hand with the backfill below (`publish --history DIR`), or copy the old dataset over.

If you set the three variables in the workflow's `env:` block, the dataset card and site pick them up. If you only change the defaults in `config.py`, you don't need to.

## Running things yourself

```sh
uv sync
uv run pytest                                            # tests; the workflow runs this first
uv run python -m ota_watch.collect                       # read FPDS now
ANTHROPIC_API_KEY=... uv run python -m ota_watch.digest  # build this week's issue
uv run python -m ota_watch.site --out site/dist          # build the site
HF_TOKEN=... uv run python -m ota_watch.hf publish       # update the dataset
```

The site builder refuses to write anywhere but a folder named `dist`.

Records signed more than 400 days ago aren't re-read daily, so a later FPDS correction to one doesn't reach the dataset by itself. To refresh them:

```sh
uv run python -m ota_watch.hf backfill DIR
HF_TOKEN=... uv run python -m ota_watch.hf publish --history DIR
```

The backfill reads one calendar year at a time into `DIR`. If it's interrupted, rerunning skips finished years.

## When something breaks

A failed run shows as a red X in the Actions tab. GitHub's own failure emails go according to each person's notification settings. This repo doesn't open an issue or send anything itself, so check the Actions tab. Open the run and read the step that failed.

- **Tests failed.** The run stops there. Nothing is collected, deployed or published until it's fixed.
- **FPDS errors.** The feed read retries on its own. If it still fails, rerun the workflow later.
- **Claude step failed.** A Monday issue is missing. Rerun the workflow with "Build this week's issue" ticked.
- **"no previous issue".** The issue builder needs an earlier issue in `data/issues/` to measure from. For the very first issue, give it a signed-date window (`--signed-from` and `--signed-to`; see the docstring at the top of `ota_watch/digest.py`). A message that a file "exists" means that date's issue is already there.
- **Push to `main` rejected.** The "Commit data" step pushes straight to `main`. If the new owner turns on branch protection, that step fails.
- **Site or dataset out of date.** Each of those steps runs after the data commit. If one fails, the data is still committed. Rerunning the workflow redoes the deploy and the publish.

## What I didn't check

I haven't run the workflow, the deploy or a Hugging Face publish. The tests and the output comparison are local only, and nothing here was pushed.
