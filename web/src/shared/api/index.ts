import createHttpClient from '@idem.agency/fetch';
import mockAdapter from '@/generated';

export const api = createHttpClient({
  mockAdapter,
});
