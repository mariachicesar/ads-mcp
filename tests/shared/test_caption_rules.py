import pytest

from shared.caption_rules import check_caption
from tests.social_fixtures import GOOD


def failed(checks): return {c["rule"] for c in checks if not c["passed"]}

def test_good_caption_passes(profile): assert failed(check_caption(profile, GOOD)) == set()
def test_missing_caption_fails_all(profile): assert failed(check_caption(profile, None)) == {"caption-hook", "caption-https-link", "caption-hashtags", "caption-city", "phone"}
def test_http_link_rejected(profile): assert "caption-https-link" in failed(check_caption(profile, GOOD.replace("https://", "http://")))
def test_other_domain_rejected(profile): assert "caption-https-link" in failed(check_caption(profile, GOOD.replace("mariachielcuis.com", "example.com")))
def test_www_subdomain_ok(profile): assert failed(check_caption(profile, GOOD.replace("https://", "https://www."))) == set()
def test_no_hashtags_fails(profile): assert failed(check_caption(profile, GOOD.split("\n\n#")[0])) == {"caption-hashtags"}
def test_hook_too_long(profile): assert "caption-hook" in failed(check_caption(profile, "x" * 151 + "\n" + GOOD))
def test_hook_cannot_be_hashtag_or_link(profile):
    assert "caption-hook" in failed(check_caption(profile, "#mariachi\n" + GOOD))
    assert "caption-hook" in failed(check_caption(profile, "https://mariachielcuis.com\n" + GOOD))
def test_city_case_accent_and_hashtag(profile):
    base = GOOD.replace("Downey", "X").replace("Whittier", "X").replace("#downey", "")
    assert "caption-city" in failed(check_caption(profile, base + " Riverside"))
    assert failed(check_caption(profile, base + " #downey")) == set()
    assert failed(check_caption(profile, base + " DOWNEY")) == set()
    assert failed(check_caption(profile, base + " #santamonica")) == set()


@pytest.mark.parametrize("tail", [".", ",", ")", ").", "\n"])
def test_link_followed_by_punctuation_ok(profile, tail):
    caption = GOOD.replace("https://mariachielcuis.com/quinceaneras", "(https://mariachielcuis.com" + tail)
    assert failed(check_caption(profile, caption)) == set()


def test_link_to_lookalike_domain_rejected(profile):
    caption = GOOD.replace("mariachielcuis.com/quinceaneras", "mariachielcuis.com.evil.com")
    assert "caption-https-link" in failed(check_caption(profile, caption))


# --- phone rule ---
BASE_PROFILE = {"websiteDomain": "mariachielcuis.com", "serviceCities": ["Downey"], "hookMaxChars": 150}
PROFILE = {**BASE_PROFILE, "phone": "626-922-0091"}
GOOD_CAPTION = GOOD.replace("📞 626-922-0091\n", "")


def _rule(checks, name):
    return next(c for c in checks if c["rule"] == name)


@pytest.mark.parametrize("text", ["Llama 626-922-0091", "(626) 922-0091", "626.922.0091", "+1 626 922 0091"])
def test_phone_rule_accepts_formats(text):
    assert _rule(check_caption(PROFILE, GOOD_CAPTION + "\n" + text), "phone")["passed"]


def test_phone_rule_blocks_missing_phone():
    check = _rule(check_caption(PROFILE, GOOD_CAPTION), "phone")
    assert not check["passed"] and check["severity"] == "blocking" and "626-922-0091" in check["message"]


def test_no_phone_rule_without_profile_phone():
    assert all(c["rule"] != "phone" for c in check_caption(BASE_PROFILE, GOOD_CAPTION))
