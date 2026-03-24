# Google in a Day — Localhost Web Crawler Dashboard

A lightweight single-machine web crawler and search dashboard built primarily with Python standard libraries.  
The system supports depth-limited crawling, duplicate prevention, bounded work queues for back pressure, live search, and real-time crawler telemetry on localhost.

## What the project does

The project exposes two main capabilities:

1. **Index / Crawl**
   - Start from an origin URL
   - Crawl up to depth `k`
   - Avoid re-processing the same normalized URL twice
   - Control load with queue capacity, worker count, and hit-rate limits

2. **Search**
   - Search indexed content while data exists locally
   - Return ranked results with:
     - `word`
     - `relevant_url`
     - `origin_url`
     - `depth`
     - `frequency`
     - `relevance_score`

A small dashboard is included for:
- starting crawler jobs
- searching the index
- viewing job history
- monitoring queue depth and crawler logs in real time

## Tech stack

This project intentionally avoids full crawler frameworks and relies on native Python functionality wherever possible.

- `urllib` for HTTP requests
- `html.parser` for parsing page content
- `http.server` for the local web server
- `threading` and `queue` for concurrency and load control
- flat files / JSONL for local persistence

## Files

- `app.py` — localhost web server and API routes
- `indexer.py` — crawler, indexing, persistence, search ranking
- `searcher.py` — search wrapper
- `crawler.html` — crawler control page
- `search.html` — search page
- `status.html` — crawler status / telemetry page
- `style.css` — shared styling
- `export_index.py` — exports internal index into quiz-compatible `data/storage/*.data`

## Local storage layout

### Internal crawler storage
The crawler’s source-of-truth data is stored under:

- `storage/visited_urls.data`
- `storage/discoveries.jsonl`
- `storage/jobs/*.json`
- `storage/index/*.jsonl`

### Quiz-compatible export layer
For the course quiz / raw-file inspection workflow, the project can export:

- `data/storage/<letter>.data`

Each line is written as:

```text
word relevant_url origin_url depth frequency
```

Run the export after crawling:

```bash
python export_index.py
```

## Running the project

Start the server:

```bash
python app.py
```

Then open:

- `http://localhost:3600/crawler.html`
- `http://localhost:3600/search.html`
- `http://localhost:3600/status.html`

## Typical workflow

1. Open `crawler.html`
2. Start one or more crawl jobs
3. Open `status.html` to monitor queue depth, progress, and logs
4. Open `search.html` to query indexed results
5. If you need quiz-format raw files, run:

```bash
python export_index.py
```

6. Inspect files such as:

```text
data/storage/p.data
```

## API overview

### Start a crawl job
```http
POST /api/crawl
```

JSON body:
```json
{
  "origin_url": "https://example.com/",
  "max_depth": 1,
  "queue_limit": 1000,
  "worker_count": 5,
  "pages_per_second": 2.0
}
```

### Clear in-memory and internal storage state
```http
POST /api/clear
```

### Global stats
```http
GET /api/stats
```

### List jobs
```http
GET /api/jobs
```

### Get a job’s live status
```http
GET /api/status/<job_id>
```

### Search
```http
GET /search?query=<word>&sortBy=relevance
GET /api/search?q=<word>
```

## Ranking logic

The implemented relevance score is:

```text
score = (frequency * 10) + 1000 - (depth * 5)
```

This makes exact matches very strong, rewards frequent occurrences, and slightly penalizes deeper discoveries.

## Notes

- The project uses **file-based local persistence**, not a separate DB engine.
- The dashboard is designed to satisfy real-time system visibility requirements.
- The crawler is intended for a **single-machine localhost assignment setting**.
- Some external sites may reject requests or close connections; this is normal for simple crawlers.

## Limitations

- No JavaScript rendering
- No distributed crawling
- No production-grade persistent queue
- No robots.txt support yet
- No full browser automation

## Author note

This project prioritizes:
- correctness
- clarity
- explainability
- architectural sensibility within an assignment-sized scope
