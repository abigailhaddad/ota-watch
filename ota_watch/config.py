"""Where this project lives. The only place its owner's names appear in the code.

Each value can be overridden with an environment variable. The defaults are the
current owner's. The GitHub workflow can't read this file; see HANDOFF.md for
the places in .github/workflows/weekly.yml that also need changing.
"""

import os

GITHUB_REPO = os.environ.get("OTA_GITHUB_REPO", "abigailhaddad/ota-watch")  # owner/name
HF_REPO_ID = os.environ.get("OTA_HF_REPO", "abigailhaddad/ota-watch")  # Hugging Face dataset, owner/name
SITE_URL = os.environ.get("OTA_SITE_URL", "https://ota-watch.abigailhaddad.com").rstrip("/")

PROJECT_URL = f"https://github.com/{GITHUB_REPO}"
DATASET_URL = f"https://huggingface.co/datasets/{HF_REPO_ID}"
ISSUE_URL = PROJECT_URL + "/issues/new"
SITE_HOST = SITE_URL.split("://", 1)[-1]
