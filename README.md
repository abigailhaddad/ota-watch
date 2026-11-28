# OTA Watch

<!-- The site, dataset and issues links in this file match the defaults in ota_watch/config.py. A new owner swaps them by hand (see HANDOFF.md). -->

Every federal Other Transaction (OT) award record newly public in FPDS, listed weekly, with a few picked out by Claude.

## Links

- Dataset (every OT record in FPDS): [abigailhaddad/ota-watch](https://huggingface.co/datasets/abigailhaddad/ota-watch) on Hugging Face; read its card before using it
- Website: [ota-watch.abigailhaddad.com](https://ota-watch.abigailhaddad.com)
- How this is made: [ota-watch.abigailhaddad.com/about](https://ota-watch.abigailhaddad.com/about)
- Sign up (Federal Data Stories, topic "OTA Watch"): [data-stories.abigailhaddad.com](https://data-stories.abigailhaddad.com/#subscribe)
- Data dictionary: [docs/data_dictionary.md](docs/data_dictionary.md); SQLite copy: [latest-data release](https://github.com/abigailhaddad/ota-watch/releases/tag/latest-data)

## How it works

- Daily collection from FPDS's public feed: [collect.py](ota_watch/collect.py), [fpds.py](ota_watch/fpds.py), scheduled by [weekly.yml](.github/workflows/weekly.yml)
- Weekly issue, picks and checks: [digest.py](ota_watch/digest.py), [opening.py](ota_watch/opening.py); prompts and model settings: [config/ota-watch.yaml](config/ota-watch.yaml)
- Website: [site.py](ota_watch/site.py); Hugging Face dataset and card: [hf.py](ota_watch/hf.py)
- Dollars are the money obligated by each action. FPDS's "total" fields on OT records are unreliable and are never shown.

## Running locally

```sh
uv sync
uv run pytest
uv run python -m ota_watch.collect                       # today's FPDS read
ANTHROPIC_API_KEY=... uv run python -m ota_watch.digest  # this week's issue
uv run python -m ota_watch.site --out site/dist
uv run python -m ota_watch.hf backfill DIR               # full history; then:
HF_TOKEN=... uv run python -m ota_watch.hf publish --history DIR
```

Spotted a wrong pick, quote or figure? [Open an issue](https://github.com/abigailhaddad/ota-watch/issues/new).

## License

Code: MIT ([LICENSE](LICENSE)). Data: FPDS records are U.S. government data.
