from sourcer.store import SupabaseStore


def test_new_secret_key_only_in_apikey_header():
    s = SupabaseStore("https://x.supabase.co", "sb_secret_abc123")
    assert s.headers["apikey"] == "sb_secret_abc123"
    assert "Authorization" not in s.headers


def test_legacy_jwt_key_also_sent_as_bearer():
    s = SupabaseStore("https://x.supabase.co", "eyJhbGciOiJIUzI1NiJ9.e30.sig")
    assert s.headers["Authorization"] == "Bearer eyJhbGciOiJIUzI1NiJ9.e30.sig"
