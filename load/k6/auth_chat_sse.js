// Skenario 2: auth + chat SSE — login lalu stream 1 turn.
// SLO: SSE first-byte p95 <= 800ms; chat <= 30 dtk p95 (lapis e2e).
import http from 'k6/http';
import { check } from 'k6';
import { Trend } from 'k6/metrics';
import { V1, authHeaders, login } from './lib.js';

const firstByte = new Trend('sse_first_byte_ms');

export const options = {
  stages: [
    { duration: '30s', target: 5 },
    { duration: '2m', target: 5 },
    { duration: '10s', target: 0 },
  ],
  thresholds: {
    http_req_failed: ['rate<0.01'],
    sse_first_byte_ms: ['p(95)<800'],
    http_req_duration: ['p(95)<30000'],
  },
};

export function setup() {
  return { token: login() };
}

export default function (data) {
  const t0 = Date.now();
  const res = http.post(
    `${V1}/chat/stream`,
    JSON.stringify({ agent: 'example_chat', input: 'halo, tes beban' }),
    { headers: authHeaders(data.token), timeout: '60s' },
  );
  check(res, { 'chat 200': (r) => r.status === 200 });
  if (res.status === 200) {
    // k6 menunggu body penuh; first-byte diaproksimasi via TTFB header timing.
    firstByte.add(res.timings.waiting);
    check(res, { 'ada turn id': (r) => !!r.headers['X-Turn-Id'] });
  }
  void t0;
}
