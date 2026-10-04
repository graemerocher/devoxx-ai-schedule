from dvxaisched.cache import ScheduleCache, normalize_key
from dvxaisched.models import DaySchedule, ScheduleResponse, rejected_schedule


def test_query_normalization():
    assert normalize_key("  Spring   Boot  AND   AI ") == "spring boot and ai"
    assert normalize_key("KUBERNETES") == "kubernetes"
    assert normalize_key(None) == ""
    assert normalize_key("   ") == ""


def test_put_and_get():
    cache = ScheduleCache(True, 120, 10)
    dummy = ScheduleResponse(True, "Valid interests", "AI Theme", "Overview of AI", [DaySchedule("monday", "2026-10-05", "Monday", [])])
    cache.put("Spring Boot & LangChain4j", dummy)

    # Case and space variations should all match
    assert cache.get("spring boot & langchain4j") is not None
    assert cache.get("   Spring   Boot   &   LangChain4j  ") is not None
    assert cache.get("Spring Boot & LangChain4j") == dummy

    # Unrelated query should miss
    assert cache.get("Quarkus") is None


def test_do_not_cache_rejected_responses():
    cache = ScheduleCache(True, 120, 10)
    cache.put("Pizza recipes", rejected_schedule("Not technical"))
    assert cache.get("Pizza recipes") is None, "Rejected responses must not be cached"


def test_max_entries_eviction():
    cache = ScheduleCache(True, 120, 10)
    dummy = ScheduleResponse(True, "OK", "Theme", "Overview", [])
    for i in range(15):
        cache.put(f"query {i}", dummy)
    assert cache.size() <= 10, "Cache size should not exceed maxEntries limit"


def test_expired_entries_are_dropped():
    cache = ScheduleCache(True, 1, 10)
    cache.put("topic", ScheduleResponse(True, "OK", "Theme", "Overview", []))
    cache._entries["topic"].created_at -= 61
    assert cache.get("topic") is None


def test_clear():
    cache = ScheduleCache(True, 120, 10)
    cache.put("topic", ScheduleResponse(True, "OK", "Theme", "Overview", []))
    assert cache.size() == 1
    cache.clear()
    assert cache.size() == 0
    assert cache.get("topic") is None
