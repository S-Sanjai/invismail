
import http from 'k6/http';
import { check, sleep } from 'k6';

export const options = {
    vus: 1,
    duration: '15s',

    thresholds: {
        http_req_failed: ['rate<0.01'],
        http_req_duration: ['p(95)<500'],
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://127.0.0.1:8000';
const TOKEN = __ENV.TOKEN || '';

export default function () {

    // 1. Liveness check
    const live = http.get(`${BASE_URL}/health/live`);

    check(live, {
        'live 200': (r) => r.status === 200,
    });

    // 2. Readiness check
    const ready = http.get(`${BASE_URL}/health/ready`);

    check(ready, {
        'ready 200': (r) => r.status === 200,
    });

    // 3. Authenticated messages check
    if (TOKEN) {

        const messages = http.get(
            `${BASE_URL}/api/v1/inboxes/me/messages`,
            {
                headers: {
                    Authorization: `Bearer ${TOKEN}`,
                },
            }
        );

        check(messages, {
            'messages 200': (r) => r.status === 200,
        });
    }

    // Keep request volume low
    sleep(2);
}
