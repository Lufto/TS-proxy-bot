"""Шифрование копий программой `age` открытым ключом (аудит R15, тот же ключ, что у бэкапов панели).

На сервере лежит только открытый ключ (`recipients.txt`): им можно зашифровать, но нельзя
расшифровать. Секретный ключ — у владельца, на сервере его нет.

`age` запускается как отдельная программа через `asyncio.create_subprocess_exec` — без
командной оболочки, аргументы передаются списком, поэтому пути с пробелами и спецсимволами
безопасны. Запуск программ спрятан за `CommandRunner`, чтобы в тестах его подменить.
"""

import asyncio
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path
from typing import Protocol

AGE_BINARY = "age"
# Больше этого времени шифрование небольшой базы идти не может — значит, что-то зависло
DEFAULT_COMMAND_TIMEOUT = timedelta(minutes=5)


class CommandFailedError(Exception):
    """Внешняя программа завершилась с ошибкой или зависла."""


class CommandRunner(Protocol):
    """Запуск внешней программы. Бросает `CommandFailedError`, если она не справилась."""

    async def run(self, args: Sequence[str]) -> None: ...


class SubprocessRunner:
    """Настоящий запуск программы в отдельном процессе, не блокируя бота."""

    def __init__(self, timeout: timedelta = DEFAULT_COMMAND_TIMEOUT) -> None:
        self._timeout = timeout

    async def run(self, args: Sequence[str]) -> None:
        program, *rest = args
        process = await asyncio.create_subprocess_exec(
            program,
            *rest,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            async with asyncio.timeout(self._timeout.total_seconds()):
                _, stderr = await process.communicate()
        except TimeoutError:
            process.kill()
            await process.wait()
            raise CommandFailedError(f"{program}: не завершилась за {self._timeout}") from None
        if process.returncode != 0:
            message = stderr.decode(errors="replace").strip()
            raise CommandFailedError(f"{program}: код {process.returncode}: {message}")


class AgeEncryptor:
    """Шифрует файл для получателей из файла открытых ключей (`age -R`)."""

    def __init__(self, recipients_file: Path, runner: CommandRunner) -> None:
        self._recipients_file = recipients_file
        self._runner = runner

    async def encrypt(self, source: Path, target: Path) -> None:
        await self._runner.run(
            [
                AGE_BINARY,
                "--encrypt",
                "--recipients-file",
                str(self._recipients_file),
                "--output",
                str(target),
                str(source),
            ]
        )
