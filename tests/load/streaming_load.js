/**
 * k6 streaming-audio load smoke.
 *
 * Each VU opens a WebSocket against /ws/streaming-audio and pumps synthetic
 * 16-kHz PCM at realistic chunk cadence (every 100ms).  We measure the time
 * from "first chunk sent" to "first transcript message received" and assert
 * P95 stays under the SLO threshold.
 *
 * Usage (local):
 *   k6 run \
 *     -e WS_URL=ws://localhost:5167/ws/streaming-audio \
 *     -e AUTH_TOKEN=$E2E_AUTH_TOKEN \
 *     tests/load/streaming_load.js
 *
 * In CI / nightly:
 *   k6 run --out json=test-reports/load/k6.json tests/load/streaming_load.js
 */

import ws from "k6/ws";
import { check } from "k6";
import { Trend, Counter } from "k6/metrics";
import encoding from "k6/encoding";

const transcriptLatency = new Trend("transcript_first_message_ms");
const errorsCounter = new Counter("ws_errors");

export const options = {
    scenarios: {
        ramp: {
            executor: "ramping-vus",
            startVUs: 1,
            stages: [
                { duration: "30s", target: 5 },
                { duration: "1m", target: 5 },
                { duration: "30s", target: 0 },
            ],
            gracefulStop: "10s",
        },
    },
    thresholds: {
        transcript_first_message_ms: ["p(95)<5000"],
        ws_errors: ["count<5"],
    },
};

const SAMPLE_RATE = 16_000;
const CHUNK_MS = 100;
const SAMPLES_PER_CHUNK = (SAMPLE_RATE * CHUNK_MS) / 1000;
const PEAK = Math.round(32_767 * 0.4);

function buildSpeechChunk() {
    const buf = new ArrayBuffer(SAMPLES_PER_CHUNK * 2);
    const view = new DataView(buf);
    for (let i = 0; i < SAMPLES_PER_CHUNK; i++) {
        const s = Math.round(PEAK * Math.sin((2 * Math.PI * 220 * i) / SAMPLE_RATE));
        view.setInt16(i * 2, s, true);
    }
    return new Uint8Array(buf);
}

const SPEECH_CHUNK = buildSpeechChunk();

export default function () {
    const url = __ENV.WS_URL || "ws://localhost:5167/ws/streaming-audio";
    const token = __ENV.AUTH_TOKEN || "test-token";

    const params = {
        headers: { Authorization: `Bearer ${token}` },
    };

    const res = ws.connect(url, params, function (socket) {
        let firstChunkSentAt = 0;
        let firstTranscriptAt = 0;

        socket.on("open", function () {
            socket.send(JSON.stringify({ type: "authenticate", token }));
        });

        socket.on("message", function (msg) {
            try {
                const parsed = JSON.parse(msg);
                if (
                    !firstTranscriptAt &&
                    (parsed.type === "final" ||
                        parsed.type === "transcript" ||
                        parsed.type === "partial")
                ) {
                    firstTranscriptAt = Date.now();
                    if (firstChunkSentAt) {
                        transcriptLatency.add(firstTranscriptAt - firstChunkSentAt);
                    }
                }
                if (parsed.type === "connected") {
                    firstChunkSentAt = Date.now();
                    socket.setInterval(function () {
                        socket.sendBinary(SPEECH_CHUNK.buffer);
                    }, CHUNK_MS);
                    socket.setTimeout(function () {
                        socket.send(JSON.stringify({ type: "stop" }));
                    }, 10_000);
                    socket.setTimeout(function () {
                        socket.close();
                    }, 12_000);
                }
            } catch (e) {
                errorsCounter.add(1);
            }
        });

        socket.on("error", function () {
            errorsCounter.add(1);
        });
    });

    check(res, { "ws handshake 101": (r) => r && r.status === 101 });
}
