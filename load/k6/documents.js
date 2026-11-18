// Skenario 4: documents — upload kecil + poll status + hapus.
// SLO: upload-accept p95 <= 400ms (202_sync, ingest async).
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
    http_req_duration: ['p(95)<2000'],
  },
};

export function setup() {
  return { token: login() };
}

const BODY = '# Dok Load\n\nParagraf tes beban k6.\n'.repeat(20);

export default function (data) {
  const form = {
    file: http.file(BODY, `k6-${__VU}-${__ITER}.md`, 'text/markdown'),
    preset: 'prose',
    ocr: 'off',
    lang: 'id',
  };
  const up = http.post(`${V1}/documents`, form, {
    headers: { Authorization: `Bearer ${data.token}` },
    timeout: '30s',
  });
  check(up, { 'upload 202': (r) => r.status === 202 });
  if (up.status !== 202) return;
  const docId = up.json().document_id;
  for (let i = 0; i < 10; i++) {
    const st = http.get(`${V1}/documents/${docId}`, {
      headers: authHeaders(data.token),
    });
    if (st.status === 200 && st.json().status === 'ready') break;
    sleep(2);
  }
  const del = http.del(
    `${V1}/documents/${docId}`, null, { headers: authHeaders(data.token) });
  check(del, { 'delete 2xx': (r) => r.status >= 200 && r.status < 300 });
}
