/**
 * Функция для очистки пустых данных в фильтре
 */
export const clearWasteData = (data: Record<string, any>): Record<string, any> => {
  const result: Record<string, any> = {};

  for (const [key, value] of Object.entries(data)) {
    if (value === null || value === undefined) continue;

    if (typeof value === 'string' && value.trim() === '') continue;

    if (Array.isArray(value) && value.length === 0) continue;

    if (
      typeof value === 'object'
      && !Array.isArray(value)
      && Object.keys(value).length === 0
    ) {
      continue;
    }

    result[key] = value;
  }

  return result;
};
