"""Central config loader. Everything reads the harness file through here."""
from __future__ import annotations
import os
from pathlib import Path
from functools import lru_cache

import yaml

ROOT = Path(__file__).resolve().parent.parent
HARNESS_PATH = ROOT / "harness" / "harness.yaml"
RUBRIC_PATH = ROOT / "rubrics" / "tr_doc_rubric.yaml"

# All RUNTIME data (SQLite DB, knowledge base, generated outputs, sync state) lives
# under DATA_ROOT. Locally this is the repo; in a deployment (e.g. Render) set the
# env var TR_DATA_DIR to a PERSISTENT disk path so data survives redeploys.
DATA_ROOT = Path(os.environ.get("TR_DATA_DIR") or ROOT)
KB_DIR = DATA_ROOT / "knowledge_base"
OUTPUTS_DIR = DATA_ROOT / "outputs"


@lru_cache(maxsize=1)
def harness() -> dict:
    with open(HARNESS_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


@lru_cache(maxsize=1)
def rubric() -> dict:
    with open(RUBRIC_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def read_text(rel_path: str) -> str:
    with open(ROOT / rel_path, "r", encoding="utf-8") as f:
        return f.read()


def system_prompt() -> str:
    return read_text("harness/system_prompt.md")


def format_spec() -> str:
    return read_text("harness/format_spec.md")


def style_guide() -> str:
    return read_text("harness/style_guide.md")


def depth_mode() -> str:
    """Rich-generation instructions, appended only when the 40-min limit is OFF."""
    return read_text("harness/depth_mode.md")


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from the project .env into the environment (once),
    without adding a dependency. Existing env vars take precedence."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


_PLACEHOLDERS = {"paste-your-key-here", "sk-ant-...", "sk-or-v1-...", ""}


def auth() -> dict:
    """Auth policy from the harness (allowed domain, admin emails)."""
    return harness().get("auth", {}) or {}


def google_client_id() -> str | None:
    """OAuth client id for Google Sign-In (from .env: GOOGLE_CLIENT_ID)."""
    _load_dotenv()
    cid = (os.environ.get("GOOGLE_CLIENT_ID") or "").strip()
    return cid or None


def auth_disabled() -> bool:
    """LOCAL-DEV login bypass. Reads AUTH_DISABLED from the environment OR .env.
    NEVER enable this in a deployed/shared setting."""
    _load_dotenv()
    return (os.environ.get("AUTH_DISABLED") or "").strip().lower() in ("1", "true", "yes")


def api_key() -> str | None:
    """Return the API key for the configured provider. Looks up the harness-named
    env var first, then common fallbacks, so an existing .env keeps working."""
    _load_dotenv()
    names = [harness().get("model", {}).get("api_key_env", "OPENROUTER_API_KEY"),
             "OPENROUTER_API_KEY", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"]
    for name in names:
        val = (os.environ.get(name) or "").strip()
        if val and val not in _PLACEHOLDERS:
            return val
    return None
