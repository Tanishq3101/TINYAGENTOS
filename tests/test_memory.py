import shutil
from pathlib import Path

from core.memory import ConversationMemory

TEST_STORE = "test_memory_store"


def _cleanup():
    if Path(TEST_STORE).exists():
        shutil.rmtree(TEST_STORE)


def test_add_and_get_context():
    _cleanup()
    mem = ConversationMemory(session_id="unit_test", persist_dir=TEST_STORE)

    mem.add("user", "What's the weather in Delhi?")
    mem.add("assistant", "Answer: Delhi: stormy, 31°C")

    context = mem.get_context()
    assert "User: What's the weather in Delhi?" in context
    assert "Assistant: Answer: Delhi: stormy, 31°C" in context

    print("✅ add() and get_context() work")
    _cleanup()


def test_trim_by_turn_count():
    _cleanup()
    mem = ConversationMemory(
        session_id="unit_test_trim", max_turns=2, max_chars=100_000, persist_dir=TEST_STORE
    )

    for i in range(5):
        mem.add("user", f"message {i}")
        mem.add("assistant", f"reply {i}")

    # max_turns=2 -> at most 2*2=4 entries kept
    assert len(mem) == 4
    context = mem.get_context()
    assert "message 4" in context  # most recent kept
    assert "message 0" not in context  # oldest dropped

    print("✅ turn-count trimming works")
    _cleanup()


def test_trim_by_char_budget():
    _cleanup()
    mem = ConversationMemory(
        session_id="unit_test_chars", max_turns=100, max_chars=50, persist_dir=TEST_STORE
    )

    mem.add("user", "a" * 30)
    mem.add("assistant", "b" * 30)
    mem.add("user", "c" * 30)

    total_chars = sum(len(e.content) for e in mem.entries)
    assert total_chars <= 50
    # most recent message must survive the trim
    assert mem.entries[-1].content == "c" * 30

    print("✅ char-budget trimming works")
    _cleanup()


def test_persistence_across_instances():
    """Simulates a process restart: create memory, add messages, then
    load a fresh ConversationMemory instance for the same session_id
    and confirm history is still there."""
    _cleanup()

    mem1 = ConversationMemory(session_id="persist_test", persist_dir=TEST_STORE)
    mem1.add("user", "My name is Tanish")
    mem1.add("assistant", "Nice to meet you, Tanish!")

    # Fresh instance, same session_id -> should load from disk
    mem2 = ConversationMemory(session_id="persist_test", persist_dir=TEST_STORE)
    assert len(mem2) == 2
    assert "Tanish" in mem2.get_context()

    print("✅ persistence across instances works")
    _cleanup()


def test_clear():
    _cleanup()
    mem = ConversationMemory(session_id="clear_test", persist_dir=TEST_STORE)
    mem.add("user", "hello")
    mem.add("assistant", "hi")
    assert len(mem) == 2

    mem.clear()
    assert len(mem) == 0
    assert mem.get_context() == ""

    print("✅ clear() works")
    _cleanup()


if __name__ == "__main__":
    test_add_and_get_context()
    test_trim_by_turn_count()
    test_trim_by_char_budget()
    test_persistence_across_instances()
    test_clear()
    print("\nAll memory tests passed.")
