"""Очистка подробностей событий от секретов."""

from tsproxy.logging_setup import MASK
from tsproxy.services.masking import Masker, sanitize_details


def test_sensitive_keys_are_hidden_at_any_depth(mask: Masker) -> None:
    details = {"order": {"payment": {"api_key": "k-123", "card_last4": "4242"}}, "days": 30}
    assert sanitize_details(details, mask) == {
        "order": {"payment": {"api_key": MASK, "card_last4": MASK}},
        "days": 30,
    }


def test_secret_inside_dict_key_is_masked(mask: Masker, bot_token: str) -> None:
    assert list(sanitize_details({f"bot{bot_token}": 1}, mask)) == [f"bot{MASK}"]
