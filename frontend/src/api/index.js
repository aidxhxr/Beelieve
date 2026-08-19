import { request, setToken } from './client';

export async function login(email, password) {
  const data = await request('/auth/login', { method: 'POST', body: { email, password } });
  setToken(data.access_token);
  return data;
}

export function logout() {
  setToken(null);
}

export const getOverview = () => request('/overview');

export const getHives = () => request('/hives');

export const getHive = (hiveId) => request(`/hives/${hiveId}`);

export const getReadings = (hiveId, { hours = 24, resolution = 'hourly' } = {}) =>
  request(`/hives/${hiveId}/readings?hours=${hours}&resolution=${resolution}`);

export const getPredictions = (hiveId, { hours = 72 } = {}) =>
  request(`/hives/${hiveId}/predictions?hours=${hours}`);

export const getAlerts = (hiveId, { limit = 50 } = {}) =>
  request(`/hives/${hiveId}/alerts?limit=${limit}`);

export const getRecommendations = (hiveId, { limit = 10 } = {}) =>
  request(`/hives/${hiveId}/recommendations?limit=${limit}`);

export const refreshRecommendations = (hiveId) =>
  request(`/hives/${hiveId}/recommendations/refresh`, { method: 'POST', timeoutMs: 35000 });

export const ackAlert = (hiveId, time) =>
  request('/alerts/ack', { method: 'POST', body: { hive_id: hiveId, time } });
