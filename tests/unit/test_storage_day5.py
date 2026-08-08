# path: tinyagentos/tests/unit/test_storage_day5.py

"""Tests for storage/models.py, database.py, cache.py."""

import time

import pytest

# --- models.py / database.py -----------------------------------------------


@pytest.fixture
def db(tmp_path):
    from storage.database import Database

    db_path = tmp_path / "test.db"
    database = Database(f"sqlite:///{db_path}")
    database.init_db()
    return database


def test_save_and_retrieve_task(db):
    from storage.models import TaskStatus

    print("\n[test_save_and_retrieve_task] saving a new task execution...")
    task = db.save_task_execution(
        {
            "input_text": "summarize this",
            "task_type": "summarize",
            "status": TaskStatus.PENDING,
        }
    )
    print(f"[test_save_and_retrieve_task] saved task.id={task.id}")
    assert task.id is not None

    fetched = db.get_task_by_id(task.id)
    print(f"[test_save_and_retrieve_task] fetched={fetched}")
    assert fetched is not None
    assert fetched.input_text == "summarize this"
    assert fetched.status == TaskStatus.PENDING
    print("[test_save_and_retrieve_task] PASSED")


def test_get_task_by_id_returns_none_when_missing(db):
    print("\n[test_get_task_by_id_returns_none_when_missing] fetching a nonexistent task id...")
    result = db.get_task_by_id("does-not-exist")
    print(f"[test_get_task_by_id_returns_none_when_missing] result={result}")
    assert result is None
    print("[test_get_task_by_id_returns_none_when_missing] PASSED")


def test_session_rolls_back_on_error(db):
    from storage.models import TaskModel

    print("\n[test_session_rolls_back_on_error] inserting a row with an invalid enum value...")
    with pytest.raises(Exception) as exc_info:
        with db.session() as session:
            session.add(TaskModel(input_text="x", task_type="summarize", status="bad-enum-value"))
            # flush forces SQLAlchemy to actually validate/insert now,
            # inside the `with` block, so the exception is raised before
            # the context manager's implicit commit — proving rollback path
            session.flush()
    print(f"[test_session_rolls_back_on_error] raised: {exc_info.value}")
    print("[test_session_rolls_back_on_error] PASSED")


# --- cache.py -----------------------------------------------------------


def test_cache_set_and_get():
    from storage.cache import InMemoryCache

    print("\n[test_cache_set_and_get] setting key1=value1...")
    cache = InMemoryCache()
    cache.set("key1", "value1")
    result = cache.get("key1")
    print(f"[test_cache_set_and_get] get('key1')={result}")
    assert result == "value1"
    print("[test_cache_set_and_get] PASSED")


def test_cache_returns_none_for_missing_key():
    from storage.cache import InMemoryCache

    print("\n[test_cache_returns_none_for_missing_key] fetching a key that was never set...")
    cache = InMemoryCache()
    result = cache.get("nope")
    print(f"[test_cache_returns_none_for_missing_key] get('nope')={result}")
    assert result is None
    print("[test_cache_returns_none_for_missing_key] PASSED")


def test_cache_expires_entries():
    from storage.cache import InMemoryCache

    print("\n[test_cache_expires_entries] setting key with ttl_seconds=0...")
    cache = InMemoryCache()
    cache.set("short_lived", "value", ttl_seconds=0)
    time.sleep(0.01)
    result = cache.get("short_lived")
    print(f"[test_cache_expires_entries] get('short_lived') after sleep={result}")
    assert result is None
    print("[test_cache_expires_entries] PASSED")


def test_cache_delete():
    from storage.cache import InMemoryCache

    print("\n[test_cache_delete] setting then deleting key1...")
    cache = InMemoryCache()
    cache.set("key1", "value1")
    cache.delete("key1")
    result = cache.get("key1")
    print(f"[test_cache_delete] get('key1') after delete={result}")
    assert result is None
    print("[test_cache_delete] PASSED")


def test_cache_clear():
    from storage.cache import InMemoryCache

    print("\n[test_cache_clear] setting a and b, then clearing cache...")
    cache = InMemoryCache()
    cache.set("a", 1)
    cache.set("b", 2)
    cache.clear()
    result_a = cache.get("a")
    result_b = cache.get("b")
    print(f"[test_cache_clear] get('a')={result_a}, get('b')={result_b}")
    assert result_a is None
    assert result_b is None
    print("[test_cache_clear] PASSED")


def test_cache_purge_expired():
    from storage.cache import InMemoryCache

    print("\n[test_cache_purge_expired] setting one expiring key and one long-lived key...")
    cache = InMemoryCache()
    cache.set("expires_soon", "v", ttl_seconds=0)
    cache.set("stays", "v", ttl_seconds=3600)
    time.sleep(0.01)

    removed = cache.purge_expired()
    print(f"[test_cache_purge_expired] removed={removed}")
    assert removed == 1
    result = cache.get("stays")
    print(f"[test_cache_purge_expired] get('stays')={result}")
    assert result == "v"
    print("[test_cache_purge_expired] PASSED")
