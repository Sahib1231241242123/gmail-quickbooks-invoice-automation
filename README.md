# Invoice Automation: Gmail → QuickBooks Desktop

Automatically reads vendor invoices from Gmail and imports them into
QuickBooks Desktop Enterprise as Bills.

---

## Quick Start

### 1. Install Python dependencies

```bash
pip install -r requirements.txt

# Windows only (for QB SDK direct mode):
pip install pywin32
```

### 2. Set up Gmail API

1. Go to https://console.cloud.google.com
2. Create a new project → Enable **Gmail API**
3. Create OAuth 2.0 credentials (Desktop App)
4. Download `credentials.json` → place in project folder
5. First run will open browser to authorize

### 3. Set Anthropic API key

```bash
# Mac/Linux:
export ANTHROPIC_API_KEY=sk-ant-...

# Windows:
set ANTHROPIC_API_KEY=sk-ant-...
```

### 4. Configure QuickBooks mode

Edit `config.py`:

```python
# Option A: IIF mode (any OS, manual QB import)
config.quickbooks.mode = "iif"
config.quickbooks.iif_output_dir = "./iif_exports"

# Option B: SDK mode (Windows only, automatic)
config.quickbooks.mode = "sdk"
config.quickbooks.company_file = "C:/QuickBooks/MyCompany.qbw"
```

### 5. Run

```bash
# Process current emails and exit:
python pipeline.py --once

# Watch Gmail continuously (every 30s):
python pipeline.py --watch

# Force IIF mode:
python pipeline.py --once --mode iif
```

---

## IIF Import (if using IIF mode)

After running, open QuickBooks Desktop:

```
File > Utilities > Import > IIF Files
```

Select files from `./iif_exports/` folder.

---

## Run Tests

```bash
python test_pipeline.py
```

Expected output:
```
✅ Format 1 (Standard US): PASS
✅ Format 2 (Minimal tech): PASS
✅ Format 3 (European/German): PASS
✅ IIF file creation: PASS
✅ IIF vendor name: PASS
✅ IIF amount accuracy: PASS
✅ IIF line item count: PASS
Results: 7/7 tests passed
```

---

## Project Structure

```
invoice_automation/
├── config.py           — All settings
├── gmail_reader.py     — Gmail OAuth2 + email parsing
├── invoice_parser.py   — Claude AI invoice extraction
├── qb_connector.py     — QuickBooks SDK (Windows COM)
├── qb_iif_exporter.py  — IIF file generator (cross-platform)
├── pipeline.py         — Main orchestrator
├── logger.py           — Logging setup
├── test_pipeline.py    — Test suite (3 invoice formats)
├── FIELD_MAPPING.md    — QB field mapping documentation
└── requirements.txt    — Python dependencies
```

---

## Troubleshooting

**"No module named 'win32com'"**
→ Run `pip install pywin32` (Windows only) or switch to IIF mode.

**"ANTHROPIC_API_KEY not set"**
→ Set environment variable (see Step 3 above).

**"QB SDK connection failed"**
→ Make sure QuickBooks Desktop is open with a company file loaded.
→ Check `config.quickbooks.company_file` path.

**Invoice parsing returns low confidence**
→ Check `./logs/` for details. Invoice may have unusual layout.
→ Parsed data is still exported; review the IIF/QB entry manually.
