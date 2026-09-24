"""Import the single path policy also bundled with the standalone Packet Skill."""
import importlib.util
from pathlib import Path

_path = Path(__file__).resolve().parent / "skills/chatgpt-agent/scripts/access_policy.py"
_spec = importlib.util.spec_from_file_location("_chatgpt_agent_access_policy", _path)
if _spec is None or _spec.loader is None:
    raise ImportError("bundled access policy is missing")
_policy = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_policy)
DENY_NAMES, DENY_GLOBS, ALLOW_GLOBS = _policy.DENY_NAMES, _policy.DENY_GLOBS, _policy.ALLOW_GLOBS
relative_path, denied, checked_path = _policy.relative_path, _policy.denied, _policy.checked_path
ignore_patterns, read_regular, atomic_write = _policy.ignore_patterns, _policy.read_regular, _policy.atomic_write
