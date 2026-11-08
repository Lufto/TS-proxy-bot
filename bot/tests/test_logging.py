import logging

import pytest

from tsproxy.logging_setup import MaskingFilter, mask_subscription_urls

BOT_TOKEN = "8889627995:AAFsecretSECRETsecret"


@pytest.fixture
def records() -> list[logging.LogRecord]:
    captured: list[logging.LogRecord] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            captured.append(record)

    handler = Capture()
    handler.addFilter(MaskingFilter([BOT_TOKEN]))
    logger = logging.getLogger("test.masking")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    yield captured
    logger.removeHandler(handler)


def text_of(record: logging.LogRecord) -> str:
    return logging.Formatter().format(record)


def test_secret_in_args_is_masked(records: list[logging.LogRecord]) -> None:
    url = "https://api.telegram.org/bot%s/getMe"
    logging.getLogger("test.masking").info("запрос к " + url, BOT_TOKEN)
    assert BOT_TOKEN not in text_of(records[0])
    assert "****" in text_of(records[0])


def test_secret_in_exception_is_masked(records: list[logging.LogRecord]) -> None:
    try:
        raise RuntimeError(f"ошибка с токеном {BOT_TOKEN}")
    except RuntimeError:
        logging.getLogger("test.masking").exception("сбой")
    assert BOT_TOKEN not in text_of(records[0])


@pytest.mark.parametrize(
    "url",
    [
        "https://sub.ts-proxy.com/AbCdEfGh12345678",
        "https://panel.ts-proxy.com/api/sub/AbCdEfGh12345678",
    ],
)
def test_subscription_url_keeps_only_tail(url: str) -> None:
    masked = mask_subscription_urls(f"ссылка {url} выдана")
    assert "AbCdEfGh1234" not in masked
    assert masked == "ссылка https://…/****5678 выдана"


def test_subscription_url_in_log_is_masked(records: list[logging.LogRecord]) -> None:
    logging.getLogger("test.masking").info("ссылка %s", "https://sub.ts-proxy.com/AbCdEfGh12345678")
    assert "AbCdEfGh1234" not in text_of(records[0])
