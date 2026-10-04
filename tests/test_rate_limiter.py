import time

from dvxaisched.rate_limiter import RateLimiter


def limiter():
    # limit: 3 requests per 2 seconds for session, 5 requests per 2 seconds for IP
    return RateLimiter(True, 3, 2, 5, 2)


def test_session_rate_limiting_within_threshold():
    rate_limiter = limiter()
    for i in range(3):
        assert rate_limiter.check_rate_limit("user-session-1", "192.168.1.10").allowed, f"Request {i + 1} should be allowed"

    rejected = rate_limiter.check_rate_limit("user-session-1", "192.168.1.10")
    assert not rejected.allowed, "Request 4 should be rejected exceeding session limit of 3"
    assert rejected.retry_after_seconds >= 1
    assert "Rate limit reached" in rejected.reason


def test_independent_sessions_under_same_ip_aggregate_limit():
    rate_limiter = limiter()
    ip = "203.0.113.1"  # Shared conference NAT IP

    for _ in range(3):
        assert rate_limiter.check_rate_limit("sess-A", ip).allowed
    assert not rate_limiter.check_rate_limit("sess-A", ip).allowed, "Session A should now be throttled"

    # Session B has its own session bucket (IP total is now 3 + 2 = 5)
    assert rate_limiter.check_rate_limit("sess-B", ip).allowed
    assert rate_limiter.check_rate_limit("sess-B", ip).allowed

    # 6th request from the IP across all sessions hits the IP aggregate limit (5)
    ip_rejected = rate_limiter.check_rate_limit("sess-C", ip)
    assert not ip_rejected.allowed, "Session C should be blocked by IP aggregate limit"
    assert "network" in ip_rejected.reason


def test_sliding_window_recovery():
    fast_limiter = RateLimiter(True, 2, 1, 10, 1)
    assert fast_limiter.check_rate_limit("fast-session", "10.0.0.1").allowed
    assert fast_limiter.check_rate_limit("fast-session", "10.0.0.1").allowed
    assert not fast_limiter.check_rate_limit("fast-session", "10.0.0.1").allowed, "Should be rate limited immediately"

    time.sleep(1.1)  # Wait for the window to expire
    assert fast_limiter.check_rate_limit("fast-session", "10.0.0.1").allowed, "Should be allowed after window expires"


def test_disabled_rate_limiter_allows_unlimited():
    disabled = RateLimiter(False, 1, 60, 1, 60)
    for _ in range(20):
        assert disabled.check_rate_limit("test-sess", "1.2.3.4").allowed


def test_reset():
    rate_limiter = limiter()
    for _ in range(3):
        assert rate_limiter.check_rate_limit("sess-reset", "172.16.0.5").allowed
    assert not rate_limiter.check_rate_limit("sess-reset", "172.16.0.5").allowed

    rate_limiter.reset()
    assert rate_limiter.check_rate_limit("sess-reset", "172.16.0.5").allowed, "Should be allowed after reset"
