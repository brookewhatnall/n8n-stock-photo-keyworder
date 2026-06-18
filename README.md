# n8n Stock Photo Keyworder

An automated pipeline that watches a local folder for new images, sends each one to the Claude AI vision API, generates professional stock photography metadata, logs everything to Google Sheets, and embeds IPTC/XMP tags directly into the image file — all without any manual approval step.

Drop an image in. Walk away. Come back to a keyworded, titled, described file ready for stock agency submission.

---

## How it works

```
Mac folder watcher
       │  POST image as base64 JSON
       ▼
n8n webhook (Receive Image from Mac)
       │
       ▼
HTTP Request → Claude claude-sonnet-4-6 vision API
       │  Returns title, description, 45-50 keywords,
       │  classification (commercial/editorial), agency list
       ▼
Code node (Parse Metadata)
       │  Normalises response, joins keyword arrays to strings
       ▼
Google Sheets (append row)
       │  Logs: Filename | Title | Description | Keywords |
       │        Classification | Agencies | Notes | Date
       ▼
Set node (Return Metadata)
       │  Returns structured JSON to watcher via HTTP response
       ▼
Mac watcher receives response
       │
       ▼
exiftool embeds IPTC IIM + XMP tags into the image file
       │
       ▼
File moved to processed/ subfolder
```

The webhook uses `responseMode: lastNode` — n8n holds the HTTP connection open until the final node completes, then returns the metadata in the response body. The Python watcher blocks (up to 120 seconds) waiting for that response, then embeds it without a second round-trip.

---

## Stack

| Component | Details |
|-----------|---------|
| **n8n** | 2.x, self-hosted (Raspberry Pi or any Linux host) |
| **Claude API** | `claude-sonnet-4-6` via direct HTTP Request node (no n8n credential store) |
| **Google Sheets** | Append rows via n8n Google Sheets node v4.5 |
| **Python watcher** | `watchdog` + `requests`, runs on Mac (or any machine with folder access) |
| **exiftool** | IPTC IIM + XMP metadata embedding |

---

## Setup

### 1. n8n workflow

1. In n8n, go to **Workflows → Import** and upload `workflow.json`.
2. Open the **Call Anthropic API** node and replace `YOUR_ANTHROPIC_API_KEY` with your key from [console.anthropic.com](https://console.anthropic.com).
3. Open the **Write to Google Sheets** node and replace `YOUR_GOOGLE_SHEETS_DOCUMENT_ID` with your sheet's ID (the long string in the URL between `/d/` and `/edit`).
4. In your Google Sheet, make sure row 1 contains exactly these headers in order:

   ```
   Filename  Title  Description  Keywords  Classification  Agencies  Notes  Date Approved
   ```

5. Connect a Google Sheets credential in n8n (OAuth2 or service account).
6. Activate the workflow. Note the webhook URL — it will be `https://YOUR_N8N_HOST/webhook/stock-keyworder`.

### 2. Mac watcher

```bash
# Install dependencies
pip3 install watchdog requests --break-system-packages
brew install exiftool

# Edit config at the top of stock-watcher.py
# Set WEBHOOK_URL, CREATOR, COPYRIGHT
# Set WATCH_DIR to your preferred folder (default: ~/Images to keyword for stock)

# Run
python3 stock-watcher.py
```

The watcher creates `processed/` and `failed/` subdirectories automatically. Successfully processed files move to `processed/`; anything that errors goes to `failed/` for manual review.

**To run at login**, create a launchd plist at `~/Library/LaunchAgents/com.yourname.stock-watcher.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.yourname.stock-watcher</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/bin/python3</string>
        <string>/Users/YOURUSERNAME/stock-watcher.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/Users/YOURUSERNAME/Library/Logs/stock-watcher.log</string>
    <key>StandardErrorPath</key>
    <string>/Users/YOURUSERNAME/Library/Logs/stock-watcher-error.log</string>
</dict>
</plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.yourname.stock-watcher.plist
```

---

## Claude prompt

The system prompt instructs Claude to return a strict JSON object with these fields:

```json
{
  "title": "Descriptive title, max 200 chars",
  "description": "2-3 sentences, max 200 chars total",
  "keywords": ["45-50 keywords, most-specific to most-general"],
  "classification": "commercial or editorial",
  "agencies": ["Adobe Stock", "Shutterstock", "..."],
  "notes": "usage restrictions or empty string"
}
```

Agency routing logic baked into the prompt:
- **Commercial** (no recognisable people): Adobe Stock, Shutterstock, Depositphotos, Dreamstime, 123RF
- **Fine art / landscapes**: add Getty Images
- **Editorial**: Alamy and Getty Images only
- **High-quality B&W**: always include Alamy

---

## Metadata written to image

exiftool writes both IPTC IIM and XMP:

| Tag | Value |
|-----|-------|
| `IPTC:ObjectName` | Title |
| `IPTC:Caption-Abstract` | Description |
| `IPTC:By-line` | Creator name |
| `IPTC:CopyrightNotice` | Copyright string |
| `IPTC:Keywords` | Each keyword as individual IPTC repeat |
| `XMP-dc:Title` | Title |
| `XMP-dc:Description` | Description |
| `XMP-dc:Creator` | Creator name |
| `XMP-dc:Rights` | Copyright string |
| `XMP-dc:Subject` | Keywords array |

---

## Known constraints

- **Image size**: Claude's vision API accepts base64 images up to ~5 MB in the request body. For large TIFFs, resize or export a JPEG proxy before dropping into the watch folder.
- **Timeout**: The watcher waits 120 seconds for n8n to respond. The Claude API call typically takes 15–40 seconds; Google Sheets append adds ~2 seconds. If you're hitting timeouts, check n8n execution logs first.
- **Google Sheets schema check**: n8n's Sheets node v4.5 reads row 1 on every append to validate column names. If you rename headers in the sheet, update the `columns.schema` array in the workflow node parameters to match, or the workflow will error.
- **SSL**: If n8n installed a self-signed cert (common on local/Tailscale setups), the watcher will retry with `verify=False` on SSL error. For production, use a proper cert.

---

## Files

| File | Purpose |
|------|---------|
| `workflow.json` | n8n workflow — import this |
| `stock-watcher.py` | Mac folder watcher + exiftool embedder |

---

## Author

Built by [Brooke Whatnall](https://brookewhatnall.com) — photographer and automation consultant based in Berlin.

Available for n8n workflow builds, AI integration, and photography automation on [Upwork](https://www.upwork.com/freelancers/~01ddd109079402da09?mp_source=share).
