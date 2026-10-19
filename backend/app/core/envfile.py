"""Small, testable helpers for locating and inspecting SIGNALTRACE configuration."""
from __future__ import annotations
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PLACEHOLDERS = {"", "your_key_here", "your_api_key_here", "paste_key_here", "change_me", "changeme", "placeholder", "your_tavily_key", "your_apinex_key"}

def resolve_env_file() -> Path:
    override = os.environ.get("SIGNALTRACE_ENV_FILE", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return (PROJECT_ROOT / ".env").resolve()

def clean_secret(value: object) -> str | None:
    if value is None:
        return None
    cleaned = str(value).strip().strip('"').strip("'")
    if cleaned.lower() in PLACEHOLDERS or any(token in cleaned.lower() for token in ("your_key", "your_api_key", "paste_your", "replace_me")):
        return None
    return cleaned or None

def config_source(name: str, env_file: Path | None = None) -> str:
    aliases={"TAVILY_API_KEY":["TAVILY_API_KEY","SIGNALTRACE_TAVILY_API_KEY"],"OPENAI_API_KEY":["OPENAI_API_KEY","SIGNALTRACE_OPENAI_API_KEY"],"OPENAI_BASE_URL":["OPENAI_BASE_URL","SIGNALTRACE_OPENAI_BASE_URL"],"OPENAI_MODEL":["OPENAI_MODEL","SIGNALTRACE_OPENAI_MODEL"]}
    names=aliases.get(name,[name])
    if any(clean_secret(os.environ.get(alias)) is not None for alias in names): return "environment"
    path = env_file or resolve_env_file()
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            key,sep,value=line.partition("=")
            if sep and key.strip() in names:
                return "dotenv" if clean_secret(value) else "unset"
    except OSError:
        pass
    return "unset"

def anchor_sqlite_url(url: str) -> str:
    """Anchor relative SQLite URLs to project root, independent of launch CWD."""
    if not url.startswith("sqlite:///") or url.startswith("sqlite:////") or url == "sqlite:///:memory:":
        return url
    raw = url[len("sqlite:///"):]
    if raw.startswith("./") or raw.startswith(".\\"):
        raw = raw[2:]
    path = Path(raw).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return "sqlite:///" + path.resolve().as_posix()
