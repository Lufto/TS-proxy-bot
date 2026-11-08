/**
 * @jest-environment node
 * @jest-environment-options {"customExportConditions":["node","node-addons","react-server"]}
 */

import { afterEach, beforeEach, expect, jest, test } from '@jest/globals';
import * as v from 'valibot';
import logger from '../../src/shared/lib/logger';
import { validationUtils } from '../../src/shared/lib/validation-utils';

type TValidationLogDto = {
  endpoint: string;
  success: boolean;
  totalErrors: number;
  omittedErrors: number;
  errors: Array<{
    kind: string;
    type: string;
    expected: string | null;
    received: string;
    message: string;
    path: string;
  }>;
};

type TLogEntry = {
  message: string;
  metadata: { error: TValidationLogDto };
};

type TCaptureOptions = {
  enabled?: string;
  terminalEnabled?: string;
};

const envKeys = [
  'VALIDATION_LOG_ENABLED',
  'VALIDATION_TERMINAL_LOG_ENABLED',
  'NO_COLOR',
  'FORCE_COLOR',
] as const;

let previousEnv: Partial<Record<typeof envKeys[number], string>>;

beforeEach(() => {
  previousEnv = Object.fromEntries(envKeys.map(key => [key, process.env[key]]));
});

afterEach(() => {
  jest.restoreAllMocks();
  for (const key of envKeys) {
    const value = previousEnv[key];
    if (value === undefined) delete process.env[key];
    else process.env[key] = value;
  }
});

function capture(
  { enabled = 'true', terminalEnabled = 'true' }: TCaptureOptions = {},
) {
  process.env.VALIDATION_LOG_ENABLED = enabled;
  process.env.VALIDATION_TERMINAL_LOG_ENABLED = terminalEnabled;
  process.env.NO_COLOR = '1';
  delete process.env.FORCE_COLOR;

  let terminal = '';
  const logs: TLogEntry[] = [];

  jest.spyOn(process.stderr, 'write').mockImplementation((chunk) => {
    terminal += String(chunk);
    return true;
  });
  jest.spyOn(logger, 'error').mockImplementation((message, metadata) => {
    logs.push({
      message: String(message),
      metadata: metadata as TLogEntry['metadata'],
    });
    return logger;
  });

  return { terminal: () => terminal, logs };
}

test('при отключённом логировании ничего не выводит в терминал и файл', () => {
  const output = capture({ enabled: 'false', terminalEnabled: 'false' });

  expect(
    validationUtils(v.object({ title: v.optional(v.string()) }), { title: 1 }),
  ).toEqual({});
  expect(output.terminal()).toBe('');
  expect(output.logs).toEqual([]);
});

test('терминальный вывод можно отключить независимо от файлового лога', () => {
  const output = capture({ terminalEnabled: 'false' });

  validationUtils(v.string(), 1, '/string');

  expect(output.terminal()).toBe('');
  expect(output.logs).toHaveLength(1);
});

test('файловый лог можно отключить независимо от терминального вывода', () => {
  const output = capture({ enabled: 'false' });

  validationUtils(v.string(), 1, '/string');

  expect(output.terminal()).toMatch(/API: \/string/);
  expect(output.logs).toEqual([]);
});

test('одна запись файлового лога содержит каждую уникальную исходную ошибку один раз', () => {
  const output = capture();
  const schema = v.object({ title: v.string(), count: v.number() });

  validationUtils(schema, { title: 1, count: 'invalid' }, '/two-errors');

  expect(output.logs).toHaveLength(1);
  const dto = output.logs[0].metadata.error;
  expect(dto.totalErrors).toBe(2);
  expect(dto.errors).toHaveLength(2);
  expect(dto.errors.map(({ path }) => path)).toEqual(['title', 'count']);
  expect(new Set(dto.errors.map(({ path }) => path)).size).toBe(2);
});

test('отчёт описывает удалённые опциональные поля и элементы массива', () => {
  const output = capture({ enabled: 'false' });
  const schema = v.object({
    title: v.optional(v.string()),
    values: v.array(v.number()),
  });

  expect(validationUtils(schema, { title: 1, values: [1, 'invalid', 2] })).toEqual({
    values: [1, 2],
  });
  expect(output.terminal()).toContain('Итог: поле удалено: title');
  expect(output.terminal()).toContain('Итог: элемент массива удалён: values[1]');
});

test('после очистки возвращает результат трансформаций Valibot', () => {
  const output = capture({ enabled: 'false', terminalEnabled: 'false' });
  const schema = v.object({
    invalid: v.optional(v.string()),
    count: v.pipe(v.string(), v.transform(Number)),
  });

  expect(validationUtils(schema, { invalid: 1, count: '2' })).toEqual({ count: 2 });
  expect(output.terminal()).toBe('');
  expect(output.logs).toEqual([]);
});

test('валидные значения boolean false сохраняются без логирования', () => {
  const output = capture();
  const input = { enabled: false, values: [false, true] };
  const schema = v.object({ enabled: v.boolean(), values: v.array(v.boolean()) });

  expect(validationUtils(schema, input)).toEqual(input);
  expect(output.terminal()).toBe('');
  expect(output.logs).toEqual([]);
});

test('терминальный и структурированный логи ограничивают внешние строки и скрывают полное чувствительное значение', () => {
  const output = capture();
  const sensitiveSuffix = 'SENSITIVE_VALUE_MUST_NOT_APPEAR';
  const longValue = `${'x'.repeat(10_000)}${sensitiveSuffix}`;
  const longMessage = `${'m'.repeat(10_000)}${sensitiveSuffix}`;
  const longEndpoint = `/${'e'.repeat(10_000)}${sensitiveSuffix}`;

  validationUtils(v.number(longMessage), longValue, longEndpoint);

  expect(output.logs).toHaveLength(1);
  const serializedLog = JSON.stringify(output.logs[0]);
  expect(serializedLog.length).toBeLessThan(3_000);
  expect(output.terminal().length).toBeLessThan(1_500);
  expect(serializedLog).not.toMatch(new RegExp(sensitiveSuffix));
  expect(output.terminal()).not.toMatch(new RegExp(sensitiveSuffix));
});

test('NO_COLOR и вывод вне TTY не содержат ANSI-последовательности', () => {
  const output = capture();
  const ansiSequence = /\u001b\[/;

  process.env.NO_COLOR = '1';
  process.env.FORCE_COLOR = '1';
  validationUtils(v.string(), 1, '/no-color');
  expect(output.terminal()).not.toMatch(ansiSequence);

  const firstReportLength = output.terminal().length;
  delete process.env.NO_COLOR;
  delete process.env.FORCE_COLOR;
  const previousIsTTY = Object.getOwnPropertyDescriptor(process.stderr, 'isTTY');
  Object.defineProperty(process.stderr, 'isTTY', { configurable: true, value: false });

  try {
    validationUtils(v.string(), 2, '/non-tty');

    expect(output.terminal().slice(firstReportLength)).not.toMatch(ansiSequence);
    expect(JSON.stringify(output.logs)).not.toMatch(ansiSequence);
  }
  finally {
    if (previousIsTTY) Object.defineProperty(process.stderr, 'isTTY', previousIsTTY);
    else Reflect.deleteProperty(process.stderr, 'isTTY');
  }
});
