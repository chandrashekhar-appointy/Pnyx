# Pnyx — End-to-End Cost Estimation

**Last updated**: June 2026  
**Current configuration** (from `.env`):
- Notes / Chat / Insights: `openai / gpt-5.4`
- Transcription (live mic): `elevenlabs` (batch mode), fallback `groq / whisper-large-v3`
- Transcription (online bot): Recall.ai → `deepgram_streaming`
- Audio for notes: sent to model when available (Gemini = file upload, OpenAI = inline base64)

> **Pricing note**: `gpt-5.4` and `gemini-3.5-flash` are frontier models released after public pricing tables. Figures below use the closest known model tier as a baseline and are marked accordingly. Verify exact pricing on the provider dashboard before presenting to finance.

---

## 1. Transcription

### 1a. Live Mic Recording (in-room meeting)

| Provider | Model | Unit | Price | 1-hour meeting |
|---|---|---|---|---|
| **ElevenLabs Scribe** *(current)* | scribe_v1 | per hour of audio | ~$0.40 | **~$0.40** |
| **Groq Whisper** *(fallback)* | whisper-large-v3 | per hour of audio | $0.111 | **$0.11** |

**How it works**: Browser microphone → AudioWorklet (downsample to 16kHz PCM) → WebSocket → ElevenLabs Scribe API (or Groq on failover). Every audio chunk is transcribed in near real-time.

**ElevenLabs vs Groq tradeoff**: ElevenLabs is 3.6× more expensive but gives significantly better Hindi/Hinglish accuracy. Groq is used as the auto-failover if ElevenLabs hits rate limits or an outage.

---

### 1b. Online Meeting Bot (Zoom/Google Meet/Teams via Recall.ai)

| Component | Provider | Price | Per 1-hour bot session |
|---|---|---|---|
| Bot recording fee | Recall.ai | ~$0.25/hr | **$0.25** |
| Real-time transcription | Deepgram Streaming (via Recall) | ~$0.0043/min | **$0.26** |
| **Total bot cost** | | | **~$0.51/hr** |

**How it works**: Recall.ai sends a virtual bot into the meeting room. The bot records and streams audio. Deepgram transcribes in real time and Recall pushes webhook events to our backend.

**Region**: `ap-northeast-1` (Tokyo) — bot is provisioned in the nearest Recall region to minimize latency in India.

---

## 2. Notes Generation (post-meeting)

Triggered once after recording stops or import pipeline completes.

### Input size for a 1-hour meeting
- Transcript text: ~10,000–18,000 words → **~13,000–24,000 tokens**
- System prompt + template: ~1,500 tokens
- **Total input**: ~15,000–25,000 tokens
- **Output** (structured JSON notes): ~2,000–4,000 tokens

### With audio (current behaviour — both Gemini and OpenAI now receive audio)

| Provider | Model | Input tokens | Output tokens | Audio cost | Total per meeting |
|---|---|---|---|---|---|
| **OpenAI** *(current)* | gpt-5.4 | ~20K @ ~$15/1M* | ~3K @ ~$60/1M* | ~compressed opus inline b64, ~$0.05 est | **~$0.63** |
| **Gemini** *(alternative)* | gemini-3.5-flash | ~20K @ ~$0.15/1M* | ~3K @ ~$0.60/1M* | 3600s audio @ ~$0.001/s = $3.60 | **~$3.61** |
| **Gemini (transcript-only)** | gemini-3.5-flash | ~20K @ ~$0.15/1M* | ~3K @ ~$0.60/1M* | none | **~$0.005** |
| **OpenAI (transcript-only)** | gpt-5.4 | ~20K @ ~$15/1M* | ~3K @ ~$60/1M* | none | **~$0.48** |

\* *gpt-5.4 pricing not yet public — using GPT-4o Turbo ($15/$60 per 1M) as conservative baseline.*  
\* *gemini-3.5-flash pricing estimated from Gemini 2.0 Flash public rates.*

**Recommendation**: OpenAI with audio is a good cost/quality balance at ~$0.63. Gemini with audio gives the best Hindi quality but audio cost alone is $3.60/meeting.

---

## 3. Live AI Insights (during recording)

The AI Host generates insight chips ("Key Insight", "Action Item", "Decision") every ~30 seconds of speech. Each call is a short structured generation.

| Call frequency | Input size | Output size |
|---|---|---|
| Every ~30s of speech | ~1,500–3,000 tokens (rolling transcript window) | ~200–500 tokens (JSON) |
| **Calls per 1-hour meeting** | ~80–120 calls | |

| Provider | Model | Cost per call | Cost per 1-hour meeting |
|---|---|---|---|
| **OpenAI** *(current)* | gpt-5.4 | ~$0.04* | **~$3.20–$4.80** |
| Gemini | gemini-3.5-flash | ~$0.0005* | **~$0.04–$0.06** |
| OpenAI | gpt-4o-mini | ~$0.0003 | **~$0.02–$0.04** |

**⚠️ Key finding**: Live AI insights with gpt-5.4 are the **single largest cost driver** at $3–5/hr — more than transcription + bot combined. Insights are short chips (not long-form); switching to `gemini-3.5-flash` or `gpt-4o-mini` saves ~$4/meeting with negligible quality difference.

---

## 4. Ask AI (in-meeting Q&A)

User-triggered — only charged when the user asks a question.

| Component | Tokens | OpenAI gpt-5.4 | Gemini 3.5 Flash |
|---|---|---|---|
| Input (transcript context + question + history) | ~5,000–8,000 | ~$0.11 | ~$0.001 |
| Output (streamed answer) | ~300–800 | ~$0.04 | ~$0.0003 |
| **Per question** | | **~$0.15** | **~$0.001** |

Typical usage: 3–8 questions per meeting = **$0.45–$1.20 per meeting** with OpenAI.

---

## 5. Catch Up Summary

User-triggered, similar cost profile to Ask AI.

| Input | Output | OpenAI gpt-5.4 | Gemini 3.5 Flash |
|---|---|---|---|
| ~4,000–10,000 tokens (time-window transcript) | ~500–1,000 tokens | ~$0.20 | ~$0.002 |

**Per catch-up request**: ~$0.20 with OpenAI.

---

## 6. Full Meeting Cost Summary

### Scenario A: In-room meeting, 1 hour, current config (OpenAI for everything)

| Line item | Cost |
|---|---|
| ElevenLabs transcription | $0.40 |
| Notes generation (OpenAI + audio) | $0.63 |
| Live AI insights (120 calls × gpt-5.4) | $4.00 |
| Ask AI (5 questions × $0.15) | $0.75 |
| Catch Up (1 request) | $0.20 |
| **Total per meeting** | **~$5.98** |

### Scenario B: Online meeting (Zoom bot), 1 hour, current config

| Line item | Cost |
|---|---|
| Recall.ai bot fee | $0.25 |
| Deepgram transcription | $0.26 |
| Notes generation (OpenAI + audio) | $0.63 |
| Live AI insights | $4.00 |
| Ask AI (5 questions) | $0.75 |
| Catch Up | $0.20 |
| **Total per meeting** | **~$6.09** |

### Scenario C: In-room meeting, 1 hour, optimised config (Gemini Flash for insights)

| Line item | Cost |
|---|---|
| ElevenLabs transcription | $0.40 |
| Notes generation (OpenAI + audio) | $0.63 |
| Live AI insights (120 calls × gemini-3.5-flash) | $0.05 |
| Ask AI (5 questions × OpenAI) | $0.75 |
| Catch Up (1 request) | $0.20 |
| **Total per meeting** | **~$2.03** |

---

## 7. Credit System (how users pay internally)

| Parameter | Value |
|---|---|
| Weekly free credits | **10,000 credits** |
| Live recording charge | **1 credit / audio chunk** (~0.3 credits/sec → **~1,080 credits/hour**) |
| Notes generation | Not charged separately (covered by recording credits) |
| Ask AI / Chat | Not charged separately (currently free) |

**1 credit ≈ $0.0055 at current OpenAI prices** (rough mapping: 10,000 credits covers ~1 hour of recording + 1 notes generation at current rates).

**Purchased credit packs**: ₹99 / ₹199 / ₹499 / ₹999 (from `backend/app/schemas/credits.py`)

---

## 8. Key Cost Reduction Levers

| Change | Env var to set | Saving per 100 meetings |
|---|---|---|
| Switch insights to `gpt-4o-mini` | `AI_PARTICIPANT_MODEL=gpt-4o-mini` | ~$390 |
| Switch insights to `gemini-3.5-flash` | `AI_PARTICIPANT_PROVIDER=gemini` + `AI_PARTICIPANT_MODEL=gemini-3.5-flash` | ~$395 |
| Switch notes to Gemini transcript-only | `NOTES_SUMMARY_PROVIDER=gemini` + `NOTES_AUDIO_ENABLED=false` | ~$60 saved vs OpenAI |
| Switch transcription to Groq only | `TRANSCRIPTION_PROVIDER=groq` | ~$29 (worse Hindi) |
| Disable audio for notes | `NOTES_AUDIO_ENABLED=false` | ~$5–10 |

**Immediate recommendation**: Set `AI_PARTICIPANT_MODEL=gpt-4o-mini` in `.env`. This single change cuts meeting cost from ~$6 to ~$2 with no perceptible quality difference for short insight chips.

---

## 9. Env Vars That Drive Cost

```bash
# Notes generation
NOTES_SUMMARY_PROVIDER=openai         # gemini = much cheaper
NOTES_SUMMARY_MODEL=gpt-5.4           # gpt-4o-mini = 50x cheaper

# Live insights — BIGGEST cost driver
AI_PARTICIPANT_PROVIDER=openai         # gemini = 80x cheaper
AI_PARTICIPANT_MODEL=gpt-5.4           # change this first

# Audio grounding for notes
NOTES_AUDIO_ENABLED=true               # false = transcript-only
NOTES_AUDIO_OPENAI_MAX_MB=20           # max audio file size for OpenAI

# Transcription
TRANSCRIPTION_PROVIDER=elevenlabs      # groq = cheaper fallback
RECALL_TRANSCRIPT_PROVIDER=deepgram_streaming
```

---

## 10. Infrastructure (Cloud Run, est.)

| Service | Est. cost/month |
|---|---|
| Backend (Cloud Run, auto-scale) | ~$20–50 |
| PostgreSQL (Neon/Supabase) | ~$25 |
| Redis (Upstash) | ~$10 |
| GCS recordings storage | ~$0.023/GB (100 meetings ≈ 500MB opus ≈ $0.01) |

---

*Pricing marked \* is estimated — verify at [platform.openai.com/pricing](https://platform.openai.com/pricing) and [ai.google.dev/pricing](https://ai.google.dev/pricing) before presenting to seniors.*
