// Helper k6 bersama: base URL, login sekali per VU, header auth.
import http from 'k6/http';

export const BASE = __ENV.BASE_URL || 'http://localhost:8000';
export const V1 = `${BASE}/api/v1`;

export function login() {
  const res = http.post(
    `${V1}/auth/login`,
    JSON.stringify({
      workspace: __ENV.K6_WORKSPACE || 'toko-a',
      username: __ENV.K6_USER || 'budi',
      password: __ENV.K6_PASSWORD || 'password',
    }),
    { headers: { 'Content-Type': 'application/json' } },
  );
  if (res.status !== 200) {
    throw new Error(`login gagal: ${res.status} ${res.body}`);
  }
  return res.json().access_token;
}

export function authHeaders(token) {
  return {
    'Content-Type': 'application/json',
    Authorization: `Bearer ${token}`,
  };
}
