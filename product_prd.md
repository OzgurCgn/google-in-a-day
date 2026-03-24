# Product PRD — Google in a Day Localhost Crawler

## Overview

This project is a localhost web crawler and search system designed for a single-machine environment.  
It provides two core capabilities:

1. `index(origin, k)` — crawl from an origin URL up to maximum depth `k`
2. `search(query)` — return relevant indexed results ranked by a simple scoring formula

A lightweight dashboard is included to control crawler jobs, monitor system state, and run searches.

## Problem statement

The assignment asks for a crawler that:
- starts from a given origin
- crawls only up to a bounded depth
- avoids revisiting the same page
- includes back pressure / controlled load
- supports search over indexed pages
- makes the state of the system visible through a simple UI / dashboard

The project should be practical, understandable, and runnable entirely on localhost.

## Goals

- Support crawl initialization from a user-provided origin URL
- Enforce a maximum crawl depth
- Prevent duplicate processing of normalized URLs
- Expose bounded queue behavior and back pressure
- Allow indexed search with relevance ranking
- Provide real-time crawler status visibility
- Store crawl state locally
- Export quiz-compatible raw storage files

## Non-goals

- Multi-machine distributed crawling
- JavaScript rendering
- Enterprise-grade search ranking
- Browser automation
- Full production queue infrastructure
- Advanced anti-bot handling

## Users

Primary users:
- instructor / TA
- evaluator
- developer running the project locally

## Functional requirements

### 1. Crawl initialization
The system must allow a user to start a crawl job with:
- origin URL
- maximum depth
- queue capacity
- worker thread count
- hit rate / pages per second

### 2. Duplicate prevention
The crawler must keep a visited set and avoid intentionally processing the same normalized URL more than once.

### 3. Depth-limited traversal
The crawler must stop expanding links beyond the configured maximum depth.

### 4. Back pressure
The crawler must use a bounded queue and visibly reflect when the queue is full or the system is throttled.

### 5. Search
The system must return relevant results for a query and include:
- matched word
- relevant URL
- origin URL
- depth
- frequency
- relevance score
- rank

### 6. Dashboard / UI
The UI must support:
- starting crawler jobs
- searching the index
- viewing job history
- monitoring queue depth and logs in real time

### 7. Persistence
The crawler must persist its local state to disk so results survive server restarts.

### 8. Quiz compatibility
The project must support exporting raw index data into `data/storage/<letter>.data` for quiz inspection.

## Ranking formula

The implemented ranking formula is:

```text
score = (frequency * 10) + 1000 - (depth * 5)
```

This intentionally favors exact word matches, rewards term frequency, and slightly penalizes deeper results.

## Data model

Each indexed record conceptually includes:
- `word`
- `relevant_url`
- `origin_url`
- `depth`
- `frequency`

## Internal storage

### Source-of-truth internal index
- `storage/visited_urls.data`
- `storage/discoveries.jsonl`
- `storage/jobs/*.json`
- `storage/index/*.jsonl`

### Exported compatibility layer
- `data/storage/<letter>.data`

The internal storage is the crawler’s primary persistent state.  
The `data/storage` folder is a derived export layer for raw-file inspection and quiz compatibility.

## System components

### `app.py`
Responsible for:
- serving dashboard pages
- exposing crawl/search/status APIs
- returning JSON responses
- clearing local state

### `indexer.py`
Responsible for:
- crawl lifecycle
- worker coordination
- queue management
- duplicate prevention
- indexing
- persistence
- ranking search results

### `searcher.py`
Responsible for:
- search access through the manager
- returning ranked results to the API layer

### UI pages
- `crawler.html`
- `search.html`
- `status.html`

## API summary

### Start crawl
```http
POST /api/crawl
```

### Clear state
```http
POST /api/clear
```

### Stats
```http
GET /api/stats
```

### List jobs
```http
GET /api/jobs
```

### Job status
```http
GET /api/status/<job_id>
```

### Search
```http
GET /search?query=<word>&sortBy=relevance
GET /api/search?q=<word>
```

## UX expectations

The user should be able to:
- create crawler jobs easily
- observe queue depth and logs
- tell whether the system is active or idle
- inspect historical jobs
- search indexed content with clear scoring feedback

## Constraints

- Must run on localhost
- Must rely mainly on Python standard libraries
- Should be small, explainable, and demonstrable
- Should prefer a correct and scoped implementation over unnecessary feature expansion

## Success criteria

The project is successful if it:
- crawls correctly up to depth `k`
- avoids duplicate processing
- demonstrates bounded queue behavior
- returns relevant search results
- shows real-time crawler state through a dashboard
- supports the raw storage / quiz workflow
