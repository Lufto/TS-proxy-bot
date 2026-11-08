export const getServiceInfo = (): {
  serviceInfo: { [x: string]: string | null };
} => {
  const utm: Record<string, string | null> = {};

  Object.entries(sessionStorage).forEach(([key, value]) => {
    utm[key] = value;
  });

  const result = {
    page: window?.location?.href || '',
    serviceInfo: {
      ...utm,
    },
  };

  return result;
};
