# KCC Project — Migration & Redeployment Plan

## Current Azure Resources (kissan-rg, Central India)

| Resource | Type | Details |
|---|---|---|
| kissan-rg | Resource Group | Container for everything, Central India region |
| kisaan-env | Container Apps Environment | Static IP: 20.204.246.195, Domain: delightfulmushroom-a8b58352.centralindia.azurecontainerapps.io |
| kisaan-api | Container App (main API) | Image: aj204/kissan-fastapi:v25, Port 8080, external, 0.5 CPU, 1GB RAM, 1-3 replicas |
| chroma | Container App (vector DB) | Image: chromadb/chroma:1.4.1, Port 8000, internal only, 0.5 CPU, 1GB RAM, 1 replica |
| kisaan-redis | Redis Enterprise | SKU: Balanced_B0, used for session management and embedding cache |

### Azure Volume Mounts
- `api-data` → mounted at `/data` → Azure File Share: `kissan-fast-api-data` (account: blobstorage0401)
- `api-session` → mounted at `/sessions` → Azure File Share

### kisaan-api Environment Variables

PORT, ACCESS_TOKEN, APP_SECRET, VERIFY_TOKEN, GRAPH_API_URL, GEMINI_API_KEY, OPENAI_API_KEY, WEATHER_API_KEY, AZURE_STORAGE_CONNECTION_STRING, AZURE_STORAGE_CONTAINER, CHROMA_HOST, CHROMA_PORT, CHROMA_TENANT, CHROMA_DATABASE, REDIS_HOST, REDIS_PORT, EMBED_CACHE_TTL_SECONDS, EMBED_CACHE_LOG_EVERY_N_CALLS, PYTHONUNBUFFERED, RAG_KB_DIR, CHROMA_COLLECTION_NAME, REDIS_PASSWORD, DATA_DIR, SESSIONS_DIR, USE_LOCAL_REDIS

### chroma Environment Variables

IS_PERSISTENT, PERSIST_DIRECTORY, ANONYMIZED_TELEMETRY

---

## What the Product Is

A WhatsApp chatbot for Haryana farmers (KCC — Kisan Call Center). Farmers message the bot in Hindi/Hinglish and get agricultural advice — weather forecasts, crop varieties, disease/pest/fertilizer guidance — all in Hindi, formatted for WhatsApp.

### How the RAG Corpus Was Built
1. Downloaded KCC dataset from Indian govt official website
2. Filtered down to Haryana data only
3. Narrowed to 37 core questions across the entire dataset
4. Used Gemini API to generate answers for all 37 questions across 125 crops
5. This produced 4,750 text files in `gemini_responses/<crop>/q_X.txt`
6. These were indexed into ChromaDB using Gemini embeddings

### The AI Pipeline (5-stage Gemini pipeline for crop advice)
1. **Multimodal Aggregation** (gemini-2.5-flash) — validates inputs are about the locked crop, extracts issues
2. **Query Decomposition** (gemini-2.5-flash) — splits compound query into atomic questions
3. **RAG Retrieval** — queries ChromaDB with Gemini embeddings
4. **RAG-Grounded Response** (gemini-3-flash-preview) — generates Hindi advice from evidence
5. **Final Auditor** (gemini-3-flash-preview) — fact-checks, formats for WhatsApp

### Conversational Flow (state machine)
GREETING → AWAITING_MENU_WEATHER_CHOICE → (Weather path OR Crop advice path)
- Weather: user sends GPS → 7-day OpenWeatherMap forecast in Hindi
- Crop advice: District selection → Crop name → Category → Collect queries (text/audio/images) → AI pipeline → Response

---

## Work Done on Feb 20, 2026

### Step 1 — Code extraction ✅ DONE
- Pulled aj204/kissan-fastapi:v25 from Docker Hub
- Extracted all files to local machine at `/Users/sandeepnair/Desktop/kisaan-api`

### Step 2 — Codebase cleanup ✅ DONE

**Deleted 9 unused files:**
- `services/audio.py` — voice processing via Whisper (never called, audio goes to Gemini multimodal)
- `services/vision.py` — image analysis via OpenAI GPT (never called, images go to Gemini multimodal)
- `services/language.py` — Hinglish/English normalization (only used by audio.py)
- `services/crop_detection.py` — old OpenAI-based crop detection (replaced by crop_name.py + crop_detector.py)
- `services/conversation copy.py` — backup
- `services/conversation working sometimes for prompt 4 before error handling.py` — backup
- `services/conversation current working backup.py` — backup
- `services/conversation_latest_workingbackup.py` — backup
- `services/crop_name copy.py` — backup

**Remaining clean service files (14):**
| File | Role |
|---|---|
| `__init__.py` | Package init |
| `config.py` | All env vars + paths |
| `conversation.py` | Main bot flow + AI pipeline |
| `crop_detector.py` | Local fuzzy crop matching (RapidFuzz + transliteration) |
| `crop_name.py` | Crop detection orchestrator (local → Gemini fallback) |
| `graph_api.py` | WhatsApp Cloud API calls (httpx async) |
| `message.py` | Incoming message parser |
| `redis_session.py` | Session state machine on Redis (5 min TTL) |
| `rag_builder.py` | RAG retrieval (ChromaDB + Gemini embeddings) |
| `rag_build.py` | Offline RAG indexing script |
| `blob_storage.py` | Azure Blob uploads (audio/images) |
| `weather.py` | 7-day weather via OpenWeatherMap |
| `status.py` | WhatsApp delivery status parser |
| `utility.py` | `set_timeout` helper |
| `safety_filter.py` | **NEW** — Banned pesticide safety layer |

### Step 3 — Banned Pesticide Safety Layer ✅ DONE

**Problem discovered:** Customer reported that the bot recommends banned chemicals (specifically Mancozeb for Guava). Root cause: the RAG corpus was built using Gemini-generated answers, and Gemini doesn't reliably distinguish "historically recommended" from "currently banned." The training data has thousands of older agricultural documents recommending Mancozeb, and Gemini pattern-matches against those during generation.

**Key insight:** Mancozeb is NOT universally banned — it's only banned for Guava, Jowar, and Tapioca. So the blocklist is **chemical + crop pairs**, not just chemical names.

**Source document:** `list_of_pesticides_which_are_banned_refused_registration_and_restricted_in_use_0.pdf` (CIB&RC India, updated 31.03.2024)

**Categories from the PDF:**
- 49 chemicals completely banned in India
- 5 chemicals banned for domestic use (export only)
- 8 chemicals withdrawn
- 18 chemicals refused registration
- 16 chemicals restricted for specific crops/uses

**What was built:**

1. **`data/banned_pesticides.json`** — Structured JSON of all banned/restricted chemicals extracted from the PDF, with crop-specific restrictions (e.g., `Mancozeb → banned_crops: ["Guava", "Jowar", "Tapioca"]`)

2. **`services/safety_filter.py`** — New module with:
   - `get_banned_chemicals_for_crop(crop)` — returns all chemicals banned for a specific crop
   - `scan_text_for_banned(text, crop)` — scans text for banned chemical names
   - `inject_rag_warnings(rag_results, crop)` — adds `safety_warnings` to RAG results before Gemini sees them
   - `get_auditor_safety_instruction(crop)` — generates crop-specific instruction for auditor prompt

3. **Two-layer safety system in `conversation.py`:**
   - **Layer 1 (after RAG retrieval, before Gemini grounded call):** `inject_rag_warnings()` scans evidence for banned chemicals and adds warnings to the JSON payload. `RAG_GROUNDED_ADVICE_SYSTEM_INSTRUCTION` updated to respect `safety_warnings` field.
   - **Layer 2 (auditor prompt):** `_run_auditor_prompt()` now accepts `crop` parameter and injects crop-specific banned chemical list via `get_auditor_safety_instruction()`. The auditor receives explicit instructions to remove any banned chemicals and suggest safe alternatives.

### Step 4 — Data files pulled from Azure ✅ DONE

Downloaded from Azure File Share (`kissan-fast-api-data`):
- `data/crops.json` (37KB) — crop detection master list with synonyms
- `data/Varieties and Sowing Time.json` (850KB) — variety/sowing data

Also copied from `kcc_project`:
- `gemini_responses/` (125 crop folders, 4,750 txt files) — RAG source corpus

### Current `data/` folder contents:
- `crops.json` — pulled from Azure
- `Varieties and Sowing Time.json` — pulled from Azure
- `banned_pesticides.json` — built from PDF

---

## What's Next (for tomorrow)

### Step 5 — Push to GitHub
- Sandeep will create the repo and provide the URL
- Set up `.gitignore` properly (exclude `.env`, `kissan-fast-api.yaml`, secrets)
- `gemini_responses/` goes into repo but NOT into Docker image (add to `.dockerignore`)
- `data/banned_pesticides.json` MUST be in Docker image (fix `.dockerignore`)

### Step 6 — CI/CD Setup (GitHub Actions)
- **Decision: Option B chosen** — automated build & deploy on push to main
- GitHub Action will: build Docker image → push to Docker Hub (Sandeep's account) → update Azure Container App
- Need: Sandeep's Docker Hub username, Azure service principal or credentials for GitHub Actions

### Step 7 — Deploy to Azure
- New Docker image under Sandeep's Docker Hub account
- Update Azure Container App to use new image
- Verify safety filter works end-to-end

### Open Items
- Docker Hub username needed from Sandeep
- `.dockerignore` needs update: add `gemini_responses/`, ensure `data/banned_pesticides.json` is included
- The `data/crops.json` and `data/Varieties and Sowing Time.json` are on Azure File Share (mounted at `/data`), NOT in the Docker image. `banned_pesticides.json` needs to be either: baked into the image OR uploaded to the same Azure File Share.
- Consider: should we also scan/clean the raw RAG corpus (`gemini_responses/`) for banned chemicals? Currently the safety filter catches them at runtime, but the source data still has them.
