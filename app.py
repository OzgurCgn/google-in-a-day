import json
import os
import shutil
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Union
from urllib.parse import parse_qs, urlparse

from indexer import Crawler, IndexerManager
from searcher import Searcher


HOST = "127.0.0.1"
PORT = 3600

manager = IndexerManager(storage_dir="storage")
crawler = Crawler(manager)
searcher = Searcher(manager)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

def read_file(path):
    full_path = os.path.join(BASE_DIR, path)
    with open(full_path, "rb") as f:
        return f.read()

class AppHandler(BaseHTTPRequestHandler):
    def _send_json(self, payload: Union[dict, list], status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, html, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def _send_css(self, css, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/css; charset=utf-8")
        self.send_header("Content-Length", str(len(css)))
        self.end_headers()
        self.wfile.write(css)

    def _read_json_body(self):
        content_length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(content_length) if content_length > 0 else b"{}"
        return json.loads(raw.decode("utf-8"))

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path in ["/", "/index.html", "/crawler.html"]:
            return self._send_html(read_file("crawler.html"))
        
        if path == "/search.html":
            return self._send_html(read_file("search.html"))
            
        if path == "/status.html":
            return self._send_html(read_file("status.html"))

        if path == "/style.css":
            return self._send_css(read_file("style.css"))

        if path == "/api/stats":
            jobs = manager.list_jobs()
            active_count = sum(1 for j in jobs if j['status'] == 'RUNNING')
            total_urls = sum(j.get('processed_count', 0) for j in jobs)
            
            real_word_count = 0
            if hasattr(manager, 'inverted_index'):
                with manager.index_lock:
                    real_word_count = len(manager.inverted_index)

            return self._send_json({
                "urls_visited": total_urls,
                "words_in_db": real_word_count,
                "active_crawlers": active_count,
                "total_created": len(jobs)
            })
        
        if path == "/api/jobs":
            return self._send_json({"jobs": manager.list_jobs()})

        if path in ["/api/search", "/search"]:
            params = parse_qs(parsed.query)
            
            if path == "/search" and "query" not in params and "q" not in params:
                return self._send_html(read_file("search.html"))

            query = params.get("query", params.get("q", [""]))[0].strip()
            limit_raw = params.get("limit", ["50"])[0]

            try:
                limit = max(1, min(200, int(limit_raw)))
            except ValueError:
                return self._send_json({"error": "Invalid limit"}, status=400)

            results = searcher.search(query, limit=limit)

            formatted_results = []
            for rank, res in enumerate(results):
                formatted_results.append({
                    "word": res.get("word", query),
                    "relevant_url": res.get("relevant_url", ""),
                    "origin_url": res.get("origin_url", ""),
                    "depth": res.get("depth", 0),
                    "frequency": res.get("frequency", 0),
                    "relevance_score": res.get("relevance_score", 0),
                    "rank": rank + 1
                })

            return self._send_json({"results": formatted_results, "count": len(formatted_results)})

        if path.startswith("/api/status/"):
            job_id = path.split("/api/status/", 1)[1]
            status_data = manager.get_job_status(job_id)
            if not status_data:
                return self._send_json({"error": "Job not found"}, status=404)
            return self._send_json(status_data)

        return self._send_json({"error": "Not found"}, status=404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
            
        if path == "/api/clear":
            if hasattr(manager, 'index_lock'):
                with manager.index_lock:
                    manager.job_statuses.clear()
                    manager.inverted_index.clear()
                    manager.discoveries.clear()
            
            if hasattr(manager, 'visited_lock'):
                with manager.visited_lock:
                    manager.visited_urls.clear()

            storage_dir = "storage"
            if os.path.exists(storage_dir):
                try:
                    shutil.rmtree(storage_dir) 
                except Exception as e:
                    print(f"Disk cleanup error: {e}")
            
            os.makedirs(os.path.join(storage_dir, "jobs"), exist_ok=True)
            os.makedirs(os.path.join(storage_dir, "index"), exist_ok=True)
            
            visited_file = os.path.join(storage_dir, "visited_urls.data")
            open(visited_file, "w").close()

            return self._send_json({"status": "success", "message": "RAM and Disk completely cleared."})

        if path == "/api/crawl":
            if "application/json" not in self.headers.get("Content-Type", ""):
                return self._send_json(
                    {"error": "Content-Type must be application/json"},
                    status=400,
                )

            try:
                body = self._read_json_body()
            except Exception:
                return self._send_json({"error": "Invalid JSON body"}, status=400)

            origin_url = str(body.get("origin_url", "")).strip()
            if not origin_url:
                return self._send_json({"error": "origin_url is required"}, status=400)

            try:
                max_depth = int(body.get("max_depth", 1))
                queue_limit = int(body.get("queue_limit", 200))
                worker_count = int(body.get("worker_count", 5))
                pages_per_second = float(body.get("pages_per_second", 2.0))
            except ValueError:
                return self._send_json({"error": "Invalid numeric field"}, status=400)

            if max_depth < 0 or max_depth > 10:
                return self._send_json(
                    {"error": "max_depth must be between 0 and 10"},
                    status=400,
                )

            if queue_limit < 1 or queue_limit > 5000:
                return self._send_json(
                    {"error": "queue_limit must be between 1 and 5000"},
                    status=400,
                )

            if worker_count < 1 or worker_count > 50:
                return self._send_json(
                    {"error": "worker_count must be between 1 and 50"},
                    status=400,
                )

            if pages_per_second <= 0:
                return self._send_json(
                    {"error": "pages_per_second must be > 0"},
                    status=400,
                )

            job_id = crawler.start_job(
                origin_url=origin_url,
                max_depth=max_depth,
                pages_per_second=pages_per_second,
                queue_limit=queue_limit,
                worker_count=worker_count,
            )

            return self._send_json({"job_id": job_id, "status": "QUEUED"}, status=201)

        return self._send_json({"error": "Not found"}, status=404)


if __name__ == "__main__":
    server = ThreadingHTTPServer((HOST, PORT), AppHandler)
    print("Server running at http://{}:{}".format(HOST, PORT))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down...")
        server.server_close()