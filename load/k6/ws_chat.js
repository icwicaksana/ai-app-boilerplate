// Skenario 5: WS chat — ticket, connect, 1 pesan, ack <= 800ms, tutup.
// SLO: ack p95 <= 800ms; koneksi stabil 30 dtk.
import http from 'k6/http';
import ws from 'k6/ws';
import { check } from 'k6';
import { Trend } from 'k6/metrics';
import { BASE, V1, authHeaders, login } from './lib.js';

const ackMs = new Trend('ws_ack_ms');

export const options = {
  stages: [
    { duration: '30s', target: 10 },
    { duration: '1m', target: 10 },
    { duration: '10s', target: 0 },
  ],
  thresholds: {
    ws_ack_ms: ['p(95)<800'],
    ws_connecting: ['p(95)<1000'],
  },
};

export function setup() {
  return { token: login() };
}

export default function (data) {
  const t = http.post(`${V1}/realtime/ticket`, JSON.stringify({}), {
    headers: authHeaders(data.token),
  });
  check(t, { 'ticket 200': (r) => r.status === 200 });
  if (t.status !== 200) return;
  const ticket = t.json().ticket;
  const url = `${BASE.replace('http', 'ws')}/api/v1/ws/chat?ticket=${ticket}`;
  const t0 = Date.now();
  const res = ws.connect(url, { protocols: ['ai-app.v1'] }, (socket) => {
    socket.on('open', () => {
      socket.send(JSON.stringify({ type: 'chat', agent: 'example_chat',
                                   input: 'halo ws' }));
    });
    socket.on('message', (msg) => {
      const m = JSON.parse(msg);
      if (m.type === 'ack') ackMs.add(Date.now() - t0);
      if (m.type === 'result' || m.type === 'error') socket.close();
    });
    socket.setTimeout(() => socket.close(), 30000);
  });
  check(res, { 'ws 101': (r) => r && r.status === 101 });
}
