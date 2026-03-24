import json
import os
import queue
import re
import threading
import time
from collections import defaultdict
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Optional, List
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.request import Request, urlopen


WORD_RE = re.compile(r"[a-zA-Z0-9]+")


def now_ts():
    return time.time()


def ensure_dir(path):
    os.makedirs(path, exist_ok=True)


def normalize_url(base_url, raw_url):
    try:
        full = urljoin(base_url, raw_url)
        parsed = urlparse(full)

        if parsed.scheme not in ("http", "https"):
            return None

        parsed = parsed._replace(fragment="")
        scheme = parsed.scheme.lower()
        netloc = parsed.netloc.lower()
        path = parsed.path or "/"

        return urlunparse((scheme, netloc, path, "", parsed.query, ""))
    except Exception:
        return None


class PageParser(HTMLParser):
    def __init__(self, base_url):
        HTMLParser.__init__(self)
        self.base_url = base_url
        self.links = set()
        self.text_parts = []
        self.title_parts = []
        self._ignored_depth = 0
        self._inside_title = False

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()

        if tag in ("script", "style", "noscript"):
            self._ignored_depth += 1

        if tag == "title":
            self._inside_title = True

        if tag == "a":
            for attr, value in attrs:
                if attr.lower() == "href":
                    normalized = normalize_url(self.base_url, value)
                    if normalized:
                        self.links.add(normalized)

    def handle_endtag(self, tag):
        tag = tag.lower()

        if tag in ("script", "style", "noscript") and self._ignored_depth > 0:
            self._ignored_depth -= 1

        if tag == "title":
            self._inside_title = False

    def handle_data(self, data):
        if self._ignored_depth > 0:
            return

        text = data.strip()
        if not text:
            return

        self.text_parts.append(text)
        if self._inside_title:
            self.title_parts.append(text)

    @property
    def visible_text(self):
        return " ".join(self.text_parts)

    @property
    def title_text(self):
        return " ".join(self.title_parts)


def tokenize(text):
    return [m.group(0).lower() for m in WORD_RE.finditer(text)]


@dataclass
class CrawlTask:
    url: str
    depth: int


@dataclass
class JobStatus:
    job_id: str
    origin_url: str
    max_depth: int
    pages_per_second: float
    queue_limit: int
    worker_count: int

    status: str = "QUEUED"
    created_at: float = field(default_factory=now_ts)
    started_at: Optional[float] = None
    finished_at: Optional[float] = None
    processed_count: int = 0
    discovered_count: int = 0
    queue_depth: int = 0
    backpressure_active: bool = False
    last_error: Optional[str] = None
    logs: List[dict] = field(default_factory=list)


class IndexerManager:
    def __init__(
        self,
        storage_dir="storage",
        default_workers=5,
        default_queue_limit=200,
        default_fetch_timeout=10,
        default_pages_per_second=2.0,
    ):
        self.storage_dir = storage_dir
        self.index_dir = os.path.join(storage_dir, "index")
        self.jobs_dir = os.path.join(storage_dir, "jobs")
        self.visited_file = os.path.join(storage_dir, "visited_urls.data")
        self.discoveries_file = os.path.join(storage_dir, "discoveries.jsonl")

        ensure_dir(self.storage_dir)
        ensure_dir(self.index_dir)
        ensure_dir(self.jobs_dir)

        for path in [self.visited_file, self.discoveries_file]:
            if not os.path.exists(path):
                open(path, "a", encoding="utf-8").close()

        self.default_workers = default_workers
        self.default_queue_limit = default_queue_limit
        self.default_fetch_timeout = default_fetch_timeout
        self.default_pages_per_second = default_pages_per_second

        self.visited_lock = threading.Lock()
        self.jobs_lock = threading.Lock()
        self.index_lock = threading.Lock()
        self.job_counter_lock = threading.Lock()
        self.bucket_locks = {}

        self._job_counter = 0

        self.visited_urls = self._load_visited()
        self.job_statuses = {}

        self.inverted_index = defaultdict(dict)
        self.discoveries = defaultdict(set)

        self._restore_from_disk()

    def _load_visited(self):
        visited = set()
        with open(self.visited_file, "r", encoding="utf-8") as f:
            for line in f:
                url = line.strip()
                if url:
                    visited.add(url)
        return visited

    def _get_bucket_lock(self, bucket):
        with self.index_lock:
            if bucket not in self.bucket_locks:
                self.bucket_locks[bucket] = threading.Lock()
            return self.bucket_locks[bucket]

    def _append_jsonl(self, path, payload):
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False) + "\n")

    def _restore_from_disk(self) -> None:
        if os.path.exists(self.discoveries_file):
            with open(self.discoveries_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line: continue
                    try:
                        rec = json.loads(line)
                        job_id = rec["job_id"]
                        relevant_url = rec["relevant_url"]
                        origin_url = rec["origin_url"]
                        depth = int(rec["depth"])
                        self.discoveries[relevant_url].add((job_id, origin_url, depth))
                    except Exception: continue

        for filename in os.listdir(self.index_dir):
            if not filename.endswith(".jsonl"): continue
            path = os.path.join(self.index_dir, filename)
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line: continue
                    try:
                        rec = json.loads(line)
                        term = rec["term"]
                        relevant_url = rec["relevant_url"]
                        origin_url = rec["origin_url"]
                        depth = int(rec["depth"])
                        frequency = int(rec["frequency"])
                        title_hit = bool(rec["title_hit"])
                        score = frequency + (3 if title_hit else 0)

                        if relevant_url not in self.inverted_index[term]:
                            self.inverted_index[term][relevant_url] = {"score": 0, "origins": set()}

                        self.inverted_index[term][relevant_url]["score"] += score
                        self.inverted_index[term][relevant_url]["origins"].add((origin_url, depth))
                    except Exception: continue

        if os.path.exists(self.jobs_dir):
            for filename in os.listdir(self.jobs_dir):
                if not filename.endswith(".json"): continue
                path = os.path.join(self.jobs_dir, filename)
                try:
                    with open(path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        job_id = data["job_id"]
                        self.job_statuses[job_id] = JobStatus(
                            job_id=data["job_id"],
                            origin_url=data["origin_url"],
                            max_depth=data["max_depth"],
                            pages_per_second=data["pages_per_second"],
                            queue_limit=data["queue_limit"],
                            worker_count=data["worker_count"],
                            status=data["status"],
                            created_at=data["created_at"],
                            started_at=data.get("started_at"),
                            finished_at=data.get("finished_at"),
                            processed_count=data["processed_count"],
                            discovered_count=data["discovered_count"],
                            queue_depth=data["queue_depth"],
                            backpressure_active=data["backpressure_active"],
                            last_error=data.get("last_error"),
                            logs=data.get("logs", [])
                        )
                except Exception as e:
                    print(f"Job restore error for {filename}: {e}")

    def next_job_id(self):
        with self.job_counter_lock:
            self._job_counter += 1
            return "job_{}_{}".format(int(time.time() * 1000), self._job_counter)

    def create_job_status(
        self,
        job_id,
        origin_url,
        max_depth,
        pages_per_second,
        queue_limit,
        worker_count,
    ):
        with self.jobs_lock:
            self.job_statuses[job_id] = JobStatus(
                job_id=job_id,
                origin_url=origin_url,
                max_depth=max_depth,
                pages_per_second=pages_per_second,
                queue_limit=queue_limit,
                worker_count=worker_count,
            )
        self._persist_job_snapshot(job_id)

    def _persist_job_snapshot(self, job_id):
        status = self.get_job_status(job_id)
        if not status:
            return

        path = os.path.join(self.jobs_dir, "{}.json".format(job_id))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(status, f, ensure_ascii=False, indent=2)

    def update_job_status(self, job_id, **updates):
        with self.jobs_lock:
            status = self.job_statuses.get(job_id)
            if not status:
                return
            for key, value in updates.items():
                if hasattr(status, key):
                    setattr(status, key, value)
        self._persist_job_snapshot(job_id)

    def add_job_log(self, job_id, level, message):
        entry = {
            "ts": now_ts(),
            "level": level,
            "message": message,
        }
        with self.jobs_lock:
            status = self.job_statuses.get(job_id)
            if status:
                status.logs.append(entry)
                if len(status.logs) > 300:
                    status.logs = status.logs[-300:]
        self._persist_job_snapshot(job_id)

    def increment_processed(self, job_id):
        with self.jobs_lock:
            status = self.job_statuses.get(job_id)
            if status:
                status.processed_count += 1
        self._persist_job_snapshot(job_id)

    def increment_discovered(self, job_id, count=1):
        with self.jobs_lock:
            status = self.job_statuses.get(job_id)
            if status:
                status.discovered_count += count
        self._persist_job_snapshot(job_id)

    def set_queue_depth(self, job_id, value):
        with self.jobs_lock:
            status = self.job_statuses.get(job_id)
            if status:
                status.queue_depth = value
        self._persist_job_snapshot(job_id)

    def set_backpressure(self, job_id, value):
        with self.jobs_lock:
            status = self.job_statuses.get(job_id)
            if status:
                status.backpressure_active = value
        self._persist_job_snapshot(job_id)

    def get_job_status(self, job_id):
        with self.jobs_lock:
            status = self.job_statuses.get(job_id)
            if not status:
                return None
            return {
                "job_id": status.job_id,
                "origin_url": status.origin_url,
                "max_depth": status.max_depth,
                "pages_per_second": status.pages_per_second,
                "queue_limit": status.queue_limit,
                "worker_count": status.worker_count,
                "status": status.status,
                "created_at": status.created_at,
                "started_at": status.started_at,
                "finished_at": status.finished_at,
                "processed_count": status.processed_count,
                "discovered_count": status.discovered_count,
                "queue_depth": status.queue_depth,
                "backpressure_active": status.backpressure_active,
                "last_error": status.last_error,
                "logs": list(status.logs),
            }

    def list_jobs(self):
        with self.jobs_lock:
            job_ids = list(self.job_statuses.keys())

        all_jobs = []
        for job_id in job_ids:
            status = self.get_job_status(job_id)
            if status:
                all_jobs.append(status)

        all_jobs.sort(key=lambda x: x["created_at"], reverse=True)
        return all_jobs

    def check_and_mark_visited(self, url):
        with self.visited_lock:
            if url in self.visited_urls:
                return True
            self.visited_urls.add(url)
            with open(self.visited_file, "a", encoding="utf-8") as f:
                f.write(url + "\n")
            return False

    def add_discovery(self, job_id, relevant_url, origin_url, depth):
        with self.index_lock:
            self.discoveries[relevant_url].add((job_id, origin_url, depth))

        self._append_jsonl(
            self.discoveries_file,
            {
                "job_id": job_id,
                "relevant_url": relevant_url,
                "origin_url": origin_url,
                "depth": depth,
            },
        )

    def add_terms(self, relevant_url, origin_url, depth, word_counts, title_terms):
        for term, frequency in word_counts.items():
            title_hit = term in title_terms
            score = frequency + (3 if title_hit else 0)

            with self.index_lock:
                if relevant_url not in self.inverted_index[term]:
                    self.inverted_index[term][relevant_url] = {
                        "score": 0,
                        "origins": set(),
                    }
                self.inverted_index[term][relevant_url]["score"] += score
                self.inverted_index[term][relevant_url]["origins"].add((origin_url, depth))

            bucket = term[0].lower() if term else "others"
            if not bucket.isalnum():
                bucket = "others"

            bucket_path = os.path.join(self.index_dir, "{}.jsonl".format(bucket))
            payload = {
                "term": term,
                "relevant_url": relevant_url,
                "origin_url": origin_url,
                "depth": depth,
                "frequency": frequency,
                "title_hit": title_hit,
            }

            bucket_lock = self._get_bucket_lock(bucket)
            with bucket_lock:
                self._append_jsonl(bucket_path, payload)
    
    def search(self, query, limit=50):
        tokens = tokenize(query)
        if not tokens:
            return []

        results = []
        
        with self.index_lock:
            for term in tokens:
                bucket = term[0].lower() if term else "others"
                if not bucket.isalnum():
                    bucket = "others"
                    
                bucket_path = os.path.join(self.index_dir, f"{bucket}.jsonl")
                
                if not os.path.exists(bucket_path):
                    continue
                    
                with open(bucket_path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line: continue
                        try:
                            rec = json.loads(line)
                            rec_term = rec["term"]
                            
                            if rec_term.lower() == term.lower():
                                exact_match = True
                            elif rec_term.lower().startswith(term.lower()):
                                exact_match = False
                            else:
                                continue
                                
                            freq = int(rec["frequency"])
                            depth = int(rec["depth"])
                            
                            # score = (frequency * 10) + 1000 (exact match bonus) - (depth * 5)
                            score = (freq * 10) - (depth * 5)
                            if exact_match:
                                score += 1000
                            else:
                                match_ratio = len(term) / len(rec_term)
                                score += int(500 * match_ratio)
                                
                            score = max(0, int(score))
                            
                            results.append({
                                "word": rec_term,
                                "relevant_url": rec["relevant_url"],
                                "origin_url": rec["origin_url"],
                                "depth": depth,
                                "frequency": freq,
                                "relevance_score": score
                            })
                        except Exception:
                            continue

        unique_results = {}
        for r in results:
            url = r["relevant_url"]
            if url not in unique_results or r["relevance_score"] > unique_results[url]["relevance_score"]:
                unique_results[url] = r

        final_results = list(unique_results.values())
        final_results.sort(key=lambda x: x["relevance_score"], reverse=True)

        return final_results[:limit]

class Crawler:
    def __init__(self, manager):
        self.manager = manager

    def start_job(
        self,
        origin_url,
        max_depth,
        pages_per_second=None,
        queue_limit=None,
        worker_count=None,
    ):
        if pages_per_second is None:
            pages_per_second = self.manager.default_pages_per_second
        if queue_limit is None:
            queue_limit = self.manager.default_queue_limit
        if worker_count is None:
            worker_count = self.manager.default_workers

        job_id = self.manager.next_job_id()
        self.manager.create_job_status(
            job_id=job_id,
            origin_url=origin_url,
            max_depth=max_depth,
            pages_per_second=pages_per_second,
            queue_limit=queue_limit,
            worker_count=worker_count,
        )

        thread = threading.Thread(
            target=self._run_job,
            args=(job_id, origin_url, max_depth, pages_per_second, queue_limit, worker_count),
            daemon=True,
        )
        thread.start()
        return job_id

    def _run_job(
        self,
        job_id,
        origin_url,
        max_depth,
        pages_per_second,
        queue_limit,
        worker_count,
    ):
        q = queue.Queue(maxsize=queue_limit)
        stop_event = threading.Event()
        rate_lock = threading.Lock()
        last_fetch_time = {"value": 0.0}

        normalized_origin = normalize_url(origin_url, origin_url)
        if not normalized_origin:
            self.manager.update_job_status(
                job_id,
                status="FAILED",
                started_at=now_ts(),
                finished_at=now_ts(),
                last_error="Invalid origin URL",
            )
            self.manager.add_job_log(job_id, "ERROR", "Invalid origin URL")
            return

        q.put(CrawlTask(url=normalized_origin, depth=0))
        self.manager.update_job_status(job_id, status="RUNNING", started_at=now_ts())
        self.manager.set_queue_depth(job_id, q.qsize())
        self.manager.add_job_log(job_id, "INFO", "Started crawl from {}".format(normalized_origin))

        def rate_limit():
            if pages_per_second <= 0:
                return
            with rate_lock:
                min_interval = 1.0 / pages_per_second
                elapsed = time.time() - last_fetch_time["value"]
                if elapsed < min_interval:
                    time.sleep(min_interval - elapsed)
                last_fetch_time["value"] = time.time()

        def worker():
            while not stop_event.is_set():
                try:
                    task = q.get(timeout=1)
                except queue.Empty:
                    continue

                self.manager.set_queue_depth(job_id, q.qsize())

                try:
                    if task.depth > max_depth:
                        continue

                    self.manager.add_discovery(job_id, task.url, normalized_origin, task.depth)

                    already_visited = self.manager.check_and_mark_visited(task.url)
                    if already_visited:
                        self.manager.add_job_log(job_id, "INFO", "Skipped visited URL: {}".format(task.url))
                        continue

                    rate_limit()
                    new_links_count = self._process_url(
                        job_id=job_id,
                        url=task.url,
                        origin_url=normalized_origin,
                        depth=task.depth,
                        q=q,
                        max_depth=max_depth,
                    )
                    self.manager.increment_processed(job_id)
                    self.manager.increment_discovered(job_id, new_links_count)

                except Exception as exc:
                    self.manager.add_job_log(job_id, "ERROR", "Worker error: {}".format(exc))
                finally:
                    self.manager.set_queue_depth(job_id, q.qsize())
                    q.task_done()

        workers = []
        for _ in range(worker_count):
            t = threading.Thread(target=worker, daemon=True)
            t.start()
            workers.append(t)

        try:
            q.join()
            stop_event.set()
            self.manager.update_job_status(job_id, status="COMPLETED", finished_at=now_ts())
            self.manager.set_backpressure(job_id, False)
            self.manager.add_job_log(job_id, "INFO", "Crawl completed")
        except Exception as exc:
            self.manager.update_job_status(
                job_id,
                status="FAILED",
                finished_at=now_ts(),
                last_error=str(exc),
            )
            self.manager.add_job_log(job_id, "ERROR", "Job failed: {}".format(exc))
            stop_event.set()

    def _process_url(
        self,
        job_id,
        url,
        origin_url,
        depth,
        q,
        max_depth,
    ):
        req = Request(
            url,
            headers={
                "User-Agent": "BrightwaveCrawler/1.0",
                "Accept": "text/html,application/xhtml+xml",
            },
        )

        with urlopen(req, timeout=self.manager.default_fetch_timeout) as response:
            status_code = getattr(response, "status", 200)
            content_type = response.headers.get("Content-Type", "")

            if status_code != 200:
                self.manager.add_job_log(
                    job_id,
                    "WARN",
                    "{} returned status {}".format(url, status_code),
                )
                return 0

            if "text/html" not in content_type.lower():
                self.manager.add_job_log(
                    job_id,
                    "INFO",
                    "Skipped non-HTML content: {}".format(url),
                )
                return 0

            html = response.read().decode("utf-8", errors="ignore")

        parser = PageParser(url)
        parser.feed(html)

        visible_text = parser.visible_text
        title_text = parser.title_text

        tokens = tokenize(visible_text)
        title_tokens = set(tokenize(title_text))

        word_counts = defaultdict(int)
        for token in tokens:
            word_counts[token] += 1

        self.manager.add_terms(
            relevant_url=url,
            origin_url=origin_url,
            depth=depth,
            word_counts=word_counts,
            title_terms=title_tokens,
        )

        new_links_count = 0

        if depth < max_depth:
            for link in parser.links:
                retry_count = 0
                max_retries = 5
                enqueued = False

                while retry_count < max_retries and not enqueued:
                    try:
                        q.put(CrawlTask(url=link, depth=depth + 1), timeout=0.3)
                        new_links_count += 1
                        enqueued = True
                        self.manager.set_queue_depth(job_id, q.qsize())
                        self.manager.set_backpressure(job_id, False)
                    except queue.Full:
                        retry_count += 1
                        self.manager.set_backpressure(job_id, True)

                        if retry_count == 1:
                            self.manager.add_job_log(
                                job_id,
                                "WARN",
                                "Back pressure active: queue is full",
                            )

                        time.sleep(0.2)

                if not enqueued:
                    self.manager.add_job_log(
                        job_id,
                        "WARN",
                        "Skipped enqueue for {} due to sustained back pressure".format(link),
                    )

        if q.qsize() == 0:
            self.manager.set_backpressure(job_id, False)

        self.manager.add_job_log(
            job_id,
            "INFO",
            "Indexed {} at depth {}".format(url, depth),
        )
        return new_links_count