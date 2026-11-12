# X (Twitter) Automation Engine 🚀

A high-impact, curated tech news and developer showcase automation engine built specifically for **X (Twitter)**.

Designed to maximize personal brand authority and Google SEO for **Aman Kumar (`@aman179102`)** through technical curation and open-source project showcases.

---

## 🌟 Key Features

1. **100% Free & Legal Imagery (Zero Copyright Risk)**:
   - Dynamic AI-powered generation via **Pollinations.ai** (no API key needed, zero cost, completely copyright-free).
   - Optional **Pexels API** integration for royalty-free stock photography.
   - Built-in **Personal Brand Glassmorphism Banner**: Every visual carries `Curated by Aman Kumar (@aman179102) | Systems & AI Engineer` to build search entity signals.

2. **Strict English & Twitter Constraints**:
   - Every tweet is drafted strictly in active, punchy **English**.
   - Enforces Twitter's **280-character budget** with accurate `t.co` URL sizing (23 characters).
   - Generates descriptive accessibility `alt_text` for every image (improving search indexing).

3. **80/20 Content Strategy (Authority + Traffic Funnel)**:
   - **80% Breaking Tech Insights**: Security advisories (CISA/NVD CVEs), AI releases (OpenAI, DeepMind, Hugging Face), Go & Systems news.
   - **20% Project Showcases**: Automated architecture highlights of your own projects (*Distributed Storage Engine*, *Real-Time Analytics Pipeline*, *PodVoice Studio*) pointing back to `github.com/aman179102`.

4. **Multi-LLM Resilience**:
   - Cascading fallback: **Google Gemini** ➔ **Groq** ➔ **OpenRouter** ➔ **Rule-based Template Writer**.

5. **Twitter API v2 & OAuth 1.0a**:
   - Native RFC 5849 OAuth 1.0a User Context signature implementation.
   - Zero bulky dependencies: built purely on `httpx`, `Pillow`, and standard library.

---

## 📂 Project Structure

```
x-automation/
├── app/
│   ├── __init__.py
│   ├── ai.py           # Multi-LLM provider fallback with JSON extraction
│   ├── compliance.py   # Rate limits, daily caps, posting windows, topic safety
│   ├── config.py       # Configuration and settings loader
│   ├── image.py        # Dynamic visual generator + branding banner overlay
│   ├── main.py         # Unified CLI interface
│   ├── models.py       # Dataclass models (Story, PostDraft, TweetResult)
│   ├── oauth.py        # RFC 5849 OAuth 1.0a signer for httpx
│   ├── scheduler.py    # Background execution and loop daemon
│   ├── select.py       # Virality ranking & candidate scoring
│   ├── sources.py      # RSS/Atom pipelines & open-source project catalog
│   ├── state.py        # State tracking (seen URLs, posting history)
│   ├── text.py         # Twitter length calculator & text normalization
│   ├── twitter.py      # Twitter API v2 Client (Media upload & tweet creation)
│   └── writer.py       # High-impact English tech editor prompt
├── assets/
│   └── fonts/          # Bundled Inter & JetBrains Mono fonts
├── output/             # Generated cards and test images
├── state/              # Local history and seen URLs state
├── .env.example        # Environment variables template
├── requirements.txt    # Lean dependencies
└── pyproject.toml
```

---

## ⚡ Quick Start

### 1. Setup Virtual Environment
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure Credentials (`.env`)
Copy `.env.example` to `.env` and fill in your Twitter API keys:
```bash
cp .env.example .env
```
Obtain these keys from the [Twitter Developer Portal](https://developer.x.com/en/portal/dashboard):
- `X_API_KEY` (Consumer Key)
- `X_API_SECRET` (Consumer Secret)
- `X_ACCESS_TOKEN` (Access Token)
- `X_ACCESS_TOKEN_SECRET` (Access Token Secret)

*(Note: App Permissions in Developer Portal must be set to **Read and Write**).*

---

## 🛠️ Usage & Commands

### Test Image Generator & Brand Banner
Tests the dynamic visual engine and outputs a branded card to `output/test_brand_card.jpg`:
```bash
.venv/bin/python -m app.main --test-image
```

### Dry Run (Preview without Posting)
Fetches latest stories, ranks virality, drafts the tweet in English, generates the card, and displays the full preview:
```bash
.venv/bin/python -m app.main --dry-run
```

### Open Source Project Showcase (Dry Run)
Simulates an architecture showcase post for your personal open-source projects:
```bash
.venv/bin/python -m app.main --dry-run --showcase
```

### Verify Twitter API Credentials
```bash
.venv/bin/python -m app.main --check-account
```

### Publish Live Tweet
```bash
.venv/bin/python -m app.main --live
```
*(Bypass posting windows/caps for testing with `--force`:)*
```bash
.venv/bin/python -m app.main --live --force
```

### Run Continuous Daemon
```bash
.venv/bin/python -m app.main --daemon --live
```

---

## 🔒 Safety & Rate Limits

- **Daily Cap**: Configurable (default `4` posts/day) to stay far below Twitter's free limit (1,500/month).
- **Posting Spacing**: Enforces minimum `120` minutes between posts to avoid spam behavior.
- **Posting Window**: Only posts between active hours (`08:00` - `23:30` IST).
- **Topic Filter**: Strictly blocks political controversy, non-tech arguments, crypto speculation, and gossip.
