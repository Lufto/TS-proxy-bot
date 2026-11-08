"""Подделки для тестов бэкапа: отправка файлов без Telegram и запуск программ без `age`."""

import asyncio
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

FAKE_AGE_HEADER = b"fake-age\n"


@dataclass(frozen=True)
class SentDocument:
    chat_id: int
    thread_id: int | None
    path: Path
    caption: str


@dataclass
class FakeDocumentSender:
    """Запоминает отправленные файлы. С `error` вместо отправки бросает эту ошибку."""

    sent: list[SentDocument] = field(default_factory=list)
    error: Exception | None = None

    async def send_document(
        self, chat_id: int, thread_id: int | None, path: Path, caption: str
    ) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append(SentDocument(chat_id, thread_id, path, caption))


@dataclass
class FakeCommandRunner:
    """Запоминает команды. Вместо шифрования `age` кладёт в `--output` вход с меткой в начале.
    С `error` вместо запуска бросает эту ошибку."""

    calls: list[list[str]] = field(default_factory=list)
    error: Exception | None = None

    async def run(self, args: Sequence[str]) -> None:
        self.calls.append(list(args))
        if self.error is not None:
            raise self.error
        output = Path(args[list(args).index("--output") + 1])
        source = Path(args[-1])
        await asyncio.to_thread(_fake_encrypt, source, output)


def _fake_encrypt(source: Path, output: Path) -> None:
    output.write_bytes(FAKE_AGE_HEADER + source.read_bytes())
