from dvxaisched.client_ip import client_ip_from, session_id_from


def test_single_ip_in_x_forwarded_for():
    assert client_ip_from("203.0.113.195", None, None) == "203.0.113.195"


def test_multiple_ips_in_x_forwarded_for_extracts_connecting_client_ip():
    # Cloud Run appends the client IP to the end of any incoming X-Forwarded-For chain
    assert client_ip_from("192.168.1.1, 10.0.0.2, 198.51.100.42", None, None) == "198.51.100.42"


def test_x_real_ip_fallback():
    assert client_ip_from(None, "198.51.100.99", None) == "198.51.100.99"


def test_remote_address_fallback():
    assert client_ip_from(None, None, "10.1.2.3") == "10.1.2.3"
    assert client_ip_from(None, None, None) == "127.0.0.1"


def test_ipv4_port_and_ipv6_brackets_are_stripped():
    assert client_ip_from("1.2.3.4:5678", None, None) == "1.2.3.4"
    assert client_ip_from("[2001:db8::1]:443", None, None) == "2001:db8::1"
    assert client_ip_from("2001:db8::2", None, None) == "2001:db8::2"


def test_session_id_from_header():
    assert session_id_from("sess-abc-123_xyz", None, "1.1.1.1") == "sess-abc-123_xyz"


def test_session_id_sanitization():
    assert session_id_from("sess<script>alert(1)</script>!", None, "1.1.1.1") == "sessscriptalert1script"


def test_session_id_from_query_param():
    assert session_id_from(None, "custom-session-token", "1.1.1.1") == "custom-session-token"


def test_session_id_fallback_to_client_ip():
    assert session_id_from(None, None, client_ip_from("203.0.113.50", None, None)) == "203.0.113.50"
