# Docling MCP Server

Convert PDFs from public URLs into markdown, extract tables, and return structured JSON — powered by [Docling](https://github.com/docling-project/docling).

## Tools

| Tool | Description |
|------|-------------|
| `docling_process_pdf` | Full processing: markdown + tables + JSON from a PDF URL |
| `docling_extract_tables` | Extract only tables from a PDF URL |
| `docling_get_markdown` | Convert a PDF URL to clean markdown text only |

---

## Local Setup

### 1. Install dependencies

```bash
pip install -r requirements.txt
```

### 2. Run locally (stdio — for Claude Desktop)

```bash
python server.py
```

### 3. Run as HTTP server (for remote hosting)

```bash
python server.py http
```

Server starts at `http://localhost:8000/mcp`

---

## Claude Desktop Config

```json
{
  "mcpServers": {
    "docling": {
      "command": "python",
      "args": ["/absolute/path/to/server.py"]
    }
  }
}
```

---

## Deploy to MCPJam

1. Push this folder to a GitHub repo
2. Go to [mcpjam.com](https://mcpjam.com) and connect your repo
3. Set the start command: `python server.py http`
4. MCPJam will provide a public MCP URL (e.g. `https://your-server.mcpjam.com/mcp`)
5. Add that URL as a Claude Connector in Claude.ai settings

---

## Usage Examples

**Full PDF processing:**
> "Use docling to process this PDF and extract all tables: https://example.com/report.pdf"

**Tables only:**
> "Extract tables from: https://example.com/data.pdf"

**Markdown only:**
> "Convert this PDF to markdown: https://example.com/document.pdf"

---

## Supported PDF Types

- Scientific papers and regulatory documents
- Financial reports with complex tables
- Multi-column layouts
- Documents with figures and captions
- Forms and structured documents
