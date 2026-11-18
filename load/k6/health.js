// Skenario 1: health — liveness + readiness tanpa auth.
// SLO platform: availability 99.9%, p95 sinkron <= 400ms.
import http from 'k6/http';
import { check } from 'k6';
import { BASE, V1 } from './lib.js';

export const options = {
  stages: [
    { duration: '30s', target: 20 },
    { duration: '1m', target: 20 },
    { duration: '10s', target: 0 },
  ],
  thresholds: {
    http_req_failed: ['rate<0.001'],
    http_req_duration: ['p(95)<400'],
  },
};

export default function () {
  for (const path of [`${V1}/livez`, `${V1}/ready`]) {
    const res = http.get(`${BASE}${path}`);
    check(res, { [`${path} 2xx`: (r) => r.status >= 200 && r.status < 300 });
  }
}
