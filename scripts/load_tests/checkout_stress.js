import http from "k6/http";
import { check, sleep } from "k6";

/**
 * Mohajon Checkout Stress Test (Epic D-01)
 *
 * Validates the Redis LUA stock reservation logic under high concurrency.
 *
 * Usage:
 * k6 run -e SHOP_ID=<id> -e PRODUCT_ID=<id> -e VARIANT_ID=<id> -e AUTH_TOKEN=<token> checkout_stress.js
 */

export const options = {
    scenarios: {
        checkout_flood: {
            executor: "constant-arrival-rate",
            rate: 50, // 50 checkouts per second
            timeUnit: "1s",
            duration: "30s",
            preAllocatedVUs: 100,
            maxVUs: 200
        }
    },
    thresholds: {
        http_req_failed: ["rate<0.01"], // Less than 1% errors (except stock out)
        http_req_duration: ["p(95)<200"] // 95% of requests should be below 200ms
    }
};

const BASE_URL = __ENV.BASE_URL || "http://localhost:8000";
const SHOP_ID = __ENV.SHOP_ID;
const PRODUCT_ID = __ENV.PRODUCT_ID;
const VARIANT_ID = __ENV.VARIANT_ID || null;
const AUTH_TOKEN = __ENV.AUTH_TOKEN;

export default function () {
    const url = `${BASE_URL}/api/v1/storefront/${SHOP_ID}/checkout/`;

    const payload = JSON.stringify({
        items: [
            {
                product_id: PRODUCT_ID,
                variant_id: VARIANT_ID,
                quantity: 1
            }
        ],
        payment_method: "COD"
    });

    const params = {
        headers: {
            "Content-Type": "application/json",
            Authorization: `Bearer ${AUTH_TOKEN}`,
            "X-Tenant-ID": SHOP_ID
        }
    };

    const res = http.post(url, payload, params);

    // We expect 201 Created until stock runs out, then 400 Bad Request
    check(res, {
        "status is 201 or 400": (r) => r.status === 201 || r.status === 400,
        "stock out handled": (r) =>
            r.status !== 400 ||
            r.body.includes("Insufficient stock") ||
            r.body.includes("Failed to reserve")
    });
}
