import 'server-only';
import * as v from 'valibot';
import logger from '@/shared/lib/logger';

export type TPath = Array<string | number>;
export type TRemoval = { path: TPath; reason: string };

const MAX_ERRORS = 50;

function shortText(value: string, limit = 180): string {
  const CONTROL_CHARACTERS = /[\u0000-\u001f\u007f-\u009f]/g;
  const text = value.slice(0, limit).replace(CONTROL_CHARACTERS, ' ');
  return value.length > limit ? `${text}…` : text;
}

function formatPath(path: TPath): string {
  if (path.length === 0) {
    return '<корень ответа>';
  }

  let result = '';

  for (const key of path) {
    if (typeof key === 'number') {
      result += `[${key}]`;
      continue;
    }

    const isSimpleProperty = /^[a-zA-Z_$][\w$]*$/.test(key);

    if (!isSimpleProperty) {
      result += `[${JSON.stringify(key)}]`;
      continue;
    }

    result += result ? `.${key}` : key;
  }

  return result;
}

function getIssuePath(issue: v.GenericIssue): TPath {
  return (issue.path ?? []).map(({ key }) => {
    if (typeof key === 'string' || typeof key === 'number') return key;
    return '<ключ>';
  });
}

function createValidationLogDto(
  endpoint: string,
  errors: readonly v.GenericIssue[],
  success: boolean,
) {
  const limitedErrors = errors.slice(0, MAX_ERRORS);

  return {
    endpoint: shortText(endpoint),
    success,
    totalErrors: errors.length,
    omittedErrors: errors.length - limitedErrors.length,
    errors: limitedErrors.map(issue => ({
      kind: shortText(issue.kind),
      type: shortText(issue.type),
      expected: typeof issue.expected === 'string' ? shortText(issue.expected) : issue.expected,
      received: shortText(issue.received),
      message: shortText(issue.message),
      path: shortText(formatPath(getIssuePath(issue))),
    })),
  };
}

function describeValue(value: unknown): string {
  if (value === undefined) return 'отсутствует (undefined)';
  if (value === null) return 'null';
  if (typeof value === 'string') return `string ${JSON.stringify(shortText(value, 100))}`;
  if (Array.isArray(value)) return `array (${value.length} элементов)`;
  if (typeof value === 'object') return 'object';
  return `${typeof value} ${shortText(String(value), 100)}`;
}

function describeError(issue: v.GenericIssue): string {
  if (issue.input === undefined) return 'обязательное поле отсутствует';
  if (issue.kind === 'schema') return 'значение не соответствует схеме';
  return shortText(issue.message);
}

function describeOutcome(path: TPath, removals: TRemoval[], success: boolean): string {
  if (!success) return 'весь ответ отклонён';

  let parentRemoval: TRemoval | undefined;
  for (const removal of removals) {
    const containsField = removal.path.every((key, index) => key === path[index]);
    if (!containsField || removal.path.length > path.length) continue;
    if (!parentRemoval || removal.path.length < parentRemoval.path.length) {
      parentRemoval = removal;
    }
  }

  if (parentRemoval) {
    return `${parentRemoval.reason}: ${formatPath(parentRemoval.path)}`;
  }
  return 'ответ сохранён после очистки';
}

function colorize(line: string): string {
  let color = 0;
  if (line.startsWith('[VALIDATION]') || line.startsWith('   Итог:')) color = 33;
  else if (line.startsWith('   Ошибка:')) color = 31;
  else if (/^(Страница:|API:|   Поле:)/.test(line)) color = 36;
  else if (/^\d+\./.test(line)) color = 1;
  if (!color) return line;
  return `\u001b[${color}m${line}\u001b[0m`;
}

/** Один отчёт по исходным данным, без внутренних объектов Valibot. */
export function logValidationReport(
  endpoint: string,
  issues: readonly v.GenericIssue[],
  removals: TRemoval[],
  success: boolean,
): void {
  if (!issues.length) return;

  const seen = new Set<string>();
  const errors = issues.filter((issue) => {
    const identity = JSON.stringify([getIssuePath(issue), issue.type, issue.message]);
    if (seen.has(identity)) return false;
    seen.add(identity);
    return true;
  });

  if (process.env.VALIDATION_TERMINAL_LOG_ENABLED === 'true') {
    const status = success ? 'ответ сохранён после очистки' : 'ответ отклонён';
    const lines = [`[VALIDATION] Ошибок: ${errors.length} · ${status}`];
    lines.push(`API: ${endpoint || '<не указан>'}`);

    for (const [index, issue] of errors.slice(0, MAX_ERRORS).entries()) {
      const path = getIssuePath(issue);

      lines.push(
        '',
        `${index + 1}. Структура ответа`,
        `   Поле: ${formatPath(path)}`,
        `   Ошибка: ${describeError(issue)}`,
      );
      const missingKey = issue.input === undefined && issue.type === 'object';
      if (!missingKey) lines.push(`   Ожидалось: ${issue.expected ?? issue.message}`);
      lines.push(
        `   Получено: ${describeValue(issue.input)}`,
        `   Итог: ${describeOutcome(path, removals, success)}`,
      );
    }
    if (errors.length > MAX_ERRORS) {
      lines.push(`Ещё ${errors.length - MAX_ERRORS} ошибок не показано (лимит ${MAX_ERRORS}).`);
    }

    const plainLines = lines.map(line => shortText(line, 500));
    const useColor = process.env.NO_COLOR === undefined
      && process.env.FORCE_COLOR !== '0'
      && (process.stderr.isTTY || Boolean(process.env.FORCE_COLOR));
    const terminalLines = useColor ? plainLines.map(colorize) : plainLines;

    process.stderr.write(`\n${terminalLines.join('\n')}\n\n`);
  }

  if (process.env.VALIDATION_LOG_ENABLED === 'true') {
    const logDto = createValidationLogDto(endpoint, errors, success);
    logger.error(
      `Validation error: ${logDto.endpoint || '<не указан>'}. Errors: ${errors.length}`,
      { error: logDto },
    );
  }
}
