export const buildQueryParams = (obj: Record<string, any>, prefix = ''): URLSearchParams => {
  const params = new URLSearchParams();

  for (const [key, value] of Object.entries(obj)) {
    if (value === undefined || value === null) continue;

    const paramKey = prefix ? `${prefix}[${key}]` : key;

    if (typeof value === 'object' && !Array.isArray(value) && !(value instanceof Date)) {
      const nestedParams = buildQueryParams(value, paramKey);
      nestedParams.forEach((val, name) => params.append(name, val));
    }
    else if (Array.isArray(value)) {
      value.forEach((item, index) => {
        if (item !== null && item !== undefined) {
          params.append(`${paramKey}[${index}]`, String(item));
        }
      });
    }
    else if (value instanceof Date) {
      params.append(paramKey, value.toISOString());
    }
    else {
      params.append(paramKey, String(value));
    }
  }

  return params;
};
