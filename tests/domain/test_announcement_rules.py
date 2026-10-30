"""Объявление в приложении клиента (задача 35): длина и запись в заголовок `announce`."""

import pytest

from tsproxy.domain.announcement import (
    ANNOUNCE_MAX_LENGTH,
    AnnouncementProblem,
    announcement_length,
    announcement_problem,
    decode_announcement,
    encode_announcement,
)


def test_normal_text_is_fine() -> None:
    assert announcement_problem("Плановые работы 1 ноября с 3:00 до 4:00 МСК") is None


def test_empty_text_is_refused() -> None:
    assert announcement_problem("   \n ") is AnnouncementProblem.EMPTY


def test_exactly_max_length_is_fine() -> None:
    assert announcement_problem("я" * ANNOUNCE_MAX_LENGTH) is None


def test_longer_than_happ_shows_is_refused() -> None:
    assert announcement_problem("я" * (ANNOUNCE_MAX_LENGTH + 1)) is AnnouncementProblem.TOO_LONG


def test_emoji_counts_as_two_characters() -> None:
    # Приложения считают длину в UTF-16: эмодзи — два символа. Считаем так же, чтобы
    # текст не обрезался ни в одном приложении
    assert announcement_length("🚀 ок") == 5
    assert announcement_problem("🚀" * (ANNOUNCE_MAX_LENGTH // 2)) is None
    assert announcement_problem("🚀" * (ANNOUNCE_MAX_LENGTH // 2) + "!") is (
        AnnouncementProblem.TOO_LONG
    )


def test_spaces_around_do_not_count() -> None:
    assert announcement_length("  ок \n") == 2


def test_encoded_as_base64_with_prefix() -> None:
    # Пример из документации Happ: «Happ the best!» → base64:SGFwcCB0aGUgYmVzdCE=
    assert encode_announcement("Happ the best!") == "base64:SGFwcCB0aGUgYmVzdCE="


def test_cyrillic_is_encoded_in_utf8_and_fits_http_header() -> None:
    value = encode_announcement("  Работы на сервере\nскоро вернёмся  ")
    assert value.isascii()
    assert value.isprintable()
    assert decode_announcement(value) == "Работы на сервере\nскоро вернёмся"


def test_encoding_refuses_text_with_problem() -> None:
    with pytest.raises(ValueError):
        encode_announcement("я" * (ANNOUNCE_MAX_LENGTH + 1))
    with pytest.raises(ValueError):
        encode_announcement(" ")


def test_plain_value_set_in_panel_by_hand_is_shown_as_is() -> None:
    assert decode_announcement("Maintenance tonight") == "Maintenance tonight"


def test_broken_base64_is_shown_as_is() -> None:
    assert decode_announcement("base64:@@@не base64") == "base64:@@@не base64"
