import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

from infrastructure.logging import logger


@dataclass
class MemoryEntry:
    role: str  # "user" or "assistant"
    content: str
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)


class ConversationMemory:
    """Rolling conversation memory for an agent.

    This gives the agent the "remembers our conversation" experience
    people expect from ChatGPT and similar assistants: recent turns
    are kept and injected into prompts so follow-up questions ("what
    about Delhi instead?") work without the user re-stating context.

    Deliberately lightweight -- no embeddings, no vector DB. True
    long-term semantic memory (Redis/FAISS) is called out as a Phase 2
    item in the project roadmap; this is the practical Phase 1 version:
    a bounded, human-readable rolling window per session, optionally
    persisted to a small JSON file so it survives process restarts.
    """

    def __init__(
        self,
        session_id: str = "default",
        max_turns: int = 10,
        max_chars: int = 2000,
        persist_dir: Optional[str] = "memory_store",
        persist: bool = True,
    ):
        self.session_id = session_id
        self.max_turns = max_turns
        self.max_chars = max_chars
        self.persist = persist
        self.persist_dir = Path(persist_dir) if persist_dir else None
        self.entries: List[MemoryEntry] = []

        if self.persist and self.persist_dir:
            self.persist_dir.mkdir(parents=True, exist_ok=True)
            self._load()

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------
    def _file_path(self) -> Path:
        # persist_dir is Optional[Path] -- every current caller (_load,
        # _save, clear) already checks `self.persist and self.persist_dir`
        # before reaching here, but that guard lives in the callers, not
        # this method. Without this explicit check, a future call site
        # that forgets the guard would hit `None / "..."` and crash with
        # a confusing AttributeError several frames from the real cause.
        # This also lets mypy narrow persist_dir to Path below.
        if self.persist_dir is None:
            raise RuntimeError(
                "_file_path() called but persist_dir is not configured "
                "(ConversationMemory was constructed with persist_dir=None)"
            )
        safe_id = "".join(c for c in self.session_id if c.isalnum() or c in ("-", "_")) or "default"
        return self.persist_dir / f"{safe_id}.json"

    def _load(self) -> None:
        path = self._file_path()
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.entries = [MemoryEntry(**item) for item in data]
            logger.info(
                f"Loaded {len(self.entries)} memory entries for " f"session '{self.session_id}'"
            )
        except Exception as e:
            logger.warning(f"Failed to load memory for session '{self.session_id}': {e}")
            self.entries = []

    def _save(self) -> None:
        if not (self.persist and self.persist_dir):
            return
        try:
            path = self._file_path()
            path.write_text(
                json.dumps([e.to_dict() for e in self.entries], indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning(f"Failed to save memory for session '{self.session_id}': {e}")

    # ------------------------------------------------------------------
    # Core API
    # ------------------------------------------------------------------
    def add(self, role: str, content: str) -> None:
        """Add a message to memory and trim to configured limits."""
        if role not in ("user", "assistant"):
            raise ValueError("role must be 'user' or 'assistant'")
        if not content or not content.strip():
            return

        self.entries.append(MemoryEntry(role=role, content=content.strip()))
        self._trim()
        self._save()

    def _trim(self) -> None:
        # Trim by turn count first (a "turn" = one user + one assistant msg)
        if self.max_turns and len(self.entries) > self.max_turns * 2:
            self.entries = self.entries[-self.max_turns * 2 :]

        # Then trim by total character budget, dropping oldest first,
        # so a handful of very long messages can't blow the prompt
        # budget even within the turn-count limit.
        total_chars = sum(len(e.content) for e in self.entries)
        while total_chars > self.max_chars and len(self.entries) > 1:
            removed = self.entries.pop(0)
            total_chars -= len(removed.content)

    def get_context(self) -> str:
        """Return recent conversation history formatted for injection
        into a prompt. Empty string if there's no history yet."""
        if not self.entries:
            return ""

        lines = []
        for entry in self.entries:
            speaker = "User" if entry.role == "user" else "Assistant"
            lines.append(f"{speaker}: {entry.content}")

        return "\n".join(lines)

    def clear(self) -> None:
        """Wipe memory for this session, in memory and on disk."""
        self.entries = []
        if self.persist and self.persist_dir:
            path = self._file_path()
            if path.exists():
                path.unlink()
        logger.info(f"Cleared memory for session '{self.session_id}'")

    def __len__(self) -> int:
        return len(self.entries)