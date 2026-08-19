const API_URL = import.meta.env.VITE_API_URL ?? 'http://localhost:8000';

export class ApiError extends Error {
  constructor(status, detail) {
    super(detail || `API error ${status}`);
    this.status = status;
  }
}

export function getToken() {
  return localStorage.getItem('beelieve_token');
}

export function setToken(token) {
  if (token) localStorage.setItem('beelieve_token', token);
  else localStorage.removeItem('beelieve_token');
}

export async function request(path, { method = 'GET', body, timeoutMs = 10000 } = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(`${API_URL}${path}`, {
      method,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...(getToken() ? { Authorization: `Bearer ${getToken()}` } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    if (!res.ok) {
      let detail;
      try {
        detail = (await res.json()).detail;
      } catch {
        /* non-JSON error body */
      }
      throw new ApiError(res.status, detail);
    }
    return res.status === 204 ? null : res.json();
  } finally {
    clearTimeout(timer);
  }
}

export default { request, getToken, setToken };
