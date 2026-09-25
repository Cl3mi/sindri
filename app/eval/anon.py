"""Salted doc-id hashing so client part numbers never enter an AI context.

Every human-readable line the eval CLI prints goes through an Anonymizer: the
drawing `T1025300_B` shows up as `a1b2c3d4`. The salt lives outside the repo
(default ~/.claude/sindri-doc-salt, mode 0600), which is what makes the hash
irreversible in practice — part numbers are short and guessable, so an unsalted
hash could be brute-forced straight back to the original.

`mapping()` produces {hash: real_id} for a local-only trace file, so a human
can still tell which drawing a finding belongs to.
"""
import hashlib
import os
from pathlib import Path
from typing import Dict, Iterable, Optional

SALT_ENV = "SINDRI_DOC_SALT"
DEFAULT_SALT_FILE = Path.home() / ".claude" / "sindri-doc-salt"
_HASH_LEN = 8


def ensure_salt(path=None) -> str:
    """Return the persistent salt, creating it on first use. The env var wins
    so a run can be reproduced elsewhere without copying the file."""
    env = os.environ.get(SALT_ENV)
    if env:
        return env
    path = Path(path) if path is not None else DEFAULT_SALT_FILE
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    path.parent.mkdir(parents=True, exist_ok=True)
    salt = os.urandom(32).hex()
    path.write_text(salt, encoding="utf-8")
    path.chmod(0o600)
    return salt


def salt_is_persistent(path=None) -> bool:
    """Whether a salt already exists, without creating one.

    False means the next ensure_salt() call mints a throwaway. That is what
    happens inside the GPU container: no SINDRI_DOC_SALT is passed in and
    ~/.claude/sindri-doc-salt is not present, so the ids in a predict log are
    hashed under a salt that dies with the container and cannot be joined to a
    locally-scored report."""
    if os.environ.get(SALT_ENV):
        return True
    path = Path(path) if path is not None else DEFAULT_SALT_FILE
    return path.exists()


def doc_hash(doc_id: str, salt: str) -> str:
    blob = f"{salt}:{doc_id}".encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:_HASH_LEN]


class Anonymizer:
    """Callable doc_id -> display id. `enabled=False` passes ids through, for a
    human running the CLI in their own terminal (`--show-ids`)."""

    def __init__(self, salt: Optional[str] = None, enabled: bool = True):
        self.enabled = enabled
        self.salt = salt if salt is not None else (ensure_salt() if enabled else "")

    def __call__(self, doc_id: str) -> str:
        if not self.enabled:
            return doc_id
        return doc_hash(doc_id, self.salt)

    def mapping(self, doc_ids: Iterable[str]) -> Dict[str, str]:
        return {self(d): d for d in doc_ids}
