'use client';

export const SaveUTM = () => {
  if (typeof window !== 'undefined') {
    const url = window.location.search.substring(1);

    const params = new URLSearchParams(url);
    [...params.keys()].forEach((key) => {
      if (key.startsWith('utm_')) {
        sessionStorage.setItem(key, params.get(key) || '');
      }
    });
  }

  return null;
};
