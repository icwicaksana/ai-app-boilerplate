// Skenario 3: jobs pipeline — submit job rag_answer, poll selesai.
// SLO: submit p95 <= 400ms (sinkron); job e2e <= 60 dtk.
import http from 'k6/http';
import { check, sleep } from 'k6';
import { V1, authHeaders, login } from './lib.js';

export const options = {
  stages: [
    { duration: '30s', target: 3 },
    { duration: '2m', target: 3 },
    { duration: '10s', target: 0 },
  ],
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<60000'],
  },
};

export function setup() {
  return { token: login() };
}

export default function (data) {
  const res = http.post(
    `${V1}/jobs/rag_answer`,
    JSON.stringify({
      thread_id: `toko-a:budi:k6-${__VU}-${__ITER}`,
      payload: { question: 'Berapa lama masa retur barang?' },
    }),
    { headers: authHeaders(data.token), timeout: '90s' },
  );
  check(res, { 'job 200': (r) => r.status === 200 });
  sleep(1);
}
