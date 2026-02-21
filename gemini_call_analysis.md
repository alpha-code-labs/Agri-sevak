# Gemini Call Analysis — KCC WhatsApp Bot

Last updated: 2026-02-21

## Overview

10 Gemini calls across 2 files (`services/conversation.py`, `services/crop_name.py`).
Analysis done to identify what could go local vs what truly needs Gemini.

**Conclusion: Keep all calls as-is.** Local replacements add complexity without solid value.

---

## PATH A: Existing Crop + Crop Advice (main path — most users)

### 1. `aggregation_multimodal` (gemini-2.5-flash, 60s timeout)
- **File:** conversation.py:1490
- **Purpose:** Validates farmer's text/audio/images are about the locked crop. Classifies inputs (LOCKED_CROP / DIFFERENT_CROP / GENERAL_AGRI / CONTACT_REQUEST). Extracts technical issues into `"Crop - [Question 1] and [Question 2]?"`.
- **Fallback:** `aggregation_text_only` (conversation.py:1506) — same prompt, text-only, fires on multimodal timeout.
- **Could go local?** Images/audio: no. Text-only single input: theoretically yes (keyword matching + prepend crop name). Not worth the complexity.

### 2. `decomposition` (gemini-2.5-flash, 60s timeout)
- **File:** conversation.py:1177
- **Purpose:** Splits compound query into atomic lines like `Wheat | What fertilizer?` and `Wheat | How to control thrips?`
- **Could go local?** Single-question: yes (just format as `Crop | query`). Multi-question: could split on "and"/"aur" but edge cases make it fragile.

### 3. RAG Retrieval (ChromaDB + Gemini Embedding)
- **File:** rag_builder.py:305
- **Already local.** Gemini embedding (~100ms) + ChromaDB vector search. Fine as-is.

### 4. `rag_grounded` (gemini-3-flash-preview, 60s timeout)
- **File:** conversation.py:1220
- **Purpose:** Synthesizes RAG evidence into Hindi agricultural advice. Handles FOUND vs MISSING queries. Respects banned chemical warnings (safety Layer 1).
- **Could go local?** No. Core LLM value — turning English RAG evidence into Hindi farmer advice.

### 5. `auditor_final` (gemini-3-flash-preview, 60s timeout)
- **File:** conversation.py:1298
- **Purpose:** Fact-checks response + formats for WhatsApp (emojis, bold, structure). Also injects safety Layer 2 (banned chemical list for the crop).
- **Could go local?** Formatting: yes (string manipulation). Fact-checking: debatable since RAG Grounded + safety filter already handle accuracy. Could merge into RAG Grounded prompt to save one call, but risks output quality degradation.

---

## PATH B: Non-Existing Crop (AI-detected new crop, no RAG data)

### 6. `advice_main` (gemini-3-flash-preview, 60s timeout)
- **File:** conversation.py:1127
- **Purpose:** Generates advice purely from Gemini's knowledge (no RAG evidence available).
- **Could go local?** No. No local data exists for new crops.

### 7. `advice_audit` (gemini-3-flash-preview, 60s timeout)
- **File:** conversation.py:1146
- **Purpose:** Fact-checks the advice_main output.
- **Could go local?** Mostly redundant if advice_main prompt is solid. Could be eliminated but adds safety margin.

Then `auditor_final` (Call 5) also runs on this path.

---

## PATH C: Variety & Sowing Time

### 8. `varieties_fetch` (gemini-3-flash-preview, 120s timeout)
- **File:** conversation.py:1341
- **Purpose:** Generates variety/sowing data when no local JSON data exists.
- **Already has local fast path.** `_get_varieties_sowing_response()` checks local JSON first. Gemini is fallback only.

### 9. `varieties_audit` (gemini-3-flash-preview, 120s timeout)
- **File:** conversation.py:1379
- **Purpose:** Audits Gemini-generated variety data for accuracy.
- **Only runs on Gemini fallback path.** Fine as-is.

---

## PATH D: Crop Detection

### 10. `_ai_detect_crop` (gemini-3-flash-preview)
- **File:** crop_name.py:258
- **Purpose:** Identifies crop from user text when local fuzzy matching (RapidFuzz + transliteration) fails.
- **Already a fallback.** Local matching handles ~90% of cases. Fine as-is.

---

## Main Path Timing (existing crop, crop advice)

```
Aggregation (~3-5s) → Decomposition (~2-3s) → RAG (~1s) → RAG Grounded (~5-10s) → Auditor (~5-10s)
Total: ~16-28s
```

## Models Used

| Model | Used for |
|---|---|
| gemini-2.5-flash | Aggregation, Decomposition (faster, simpler tasks) |
| gemini-3-flash-preview | RAG Grounded, Auditor, Advice, Varieties, Crop Detection (higher quality) |
| gemini-embedding-001 | RAG vector embeddings |
