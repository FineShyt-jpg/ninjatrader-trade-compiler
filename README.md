# NinjaTrader Trade History Compiler

A browser-based tool to merge multiple NinjaTrader daily export files (CSV / XLSX) into one consolidated CSV per account.

## Features
- Drag-and-drop file upload
- Auto-detects NinjaTrader header rows (skips metadata at top of file)
- Deduplicates exact duplicate rows
- Sorts by entry time
- Real-time progress bar
- One-click CSV download

## Setup

### Requirements
- Python 3.10+

### Install & Run
```bash
pip install -r requirements.txt
python app.py
```

Then open your browser to `http://localhost:5000`

## Roadmap
- [ ] Multi-account management (separate compile sessions per account)
- [ ] NinjaTrader direct data pull via database/API automation
- [ ] Per-account analytics dashboard
- [ ] XLSX output option
