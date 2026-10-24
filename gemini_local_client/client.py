"""Standard-library-only SDK. No credentials are embedded in this package."""
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import ssl
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, quote
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler, ProxyHandler
import uuid

TERMINAL = {"completed", "partial", "failed", "cancelled", "needs_review"}


class ClientError(Exception):
    def __init__(self, status, code, message):
        super().__init__(message)
        self.status, self.code = status, code


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # Never forward a bearer key to another location.


def digest(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            h.update(block)
    return h.hexdigest()


class Client:
    def __init__(self, base_url="http://127.0.0.1:8765", api_key=None, certificate=None, timeout=30):
        self.base = base_url.rstrip("/")
        parsed = urlsplit(self.base)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.query or parsed.fragment or parsed.path:
            raise ValueError("Use an API origin such as http://127.0.0.1:8765.")
        try:
            self.local = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            self.local = parsed.hostname.lower() == "localhost"
        if parsed.scheme == "http" and not self.local:
            raise ValueError("LAN requires HTTPS and the desktop's exported certificate.")
        self.key = api_key or os.environ.get("GEMINI_LOCAL_API_KEY", "")
        if not self.key:
            raise ValueError("Set GEMINI_LOCAL_API_KEY or pass api_key.")
        self.timeout = timeout
        context = ssl.create_default_context(cafile=str(certificate) if certificate else None)
        self.opener = build_opener(ProxyHandler({}), HTTPSHandler(context=context), NoRedirect())

    def _open(self, path, method="GET", data=None, headers=None):
        if not path.startswith("/v1/") or path.startswith("//"):
            raise ValueError("Invalid API path.")
        request_headers = {"Authorization": "Bearer " + self.key}
        request_headers.update(headers or {})
        body = None
        if data is not None:
            body = json.dumps(data).encode()
            request_headers["Content-Type"] = "application/json"
        request = Request(self.base + path, data=body, headers=request_headers, method=method)
        try:
            return self.opener.open(request, timeout=self.timeout)
        except HTTPError as error:
            raw = error.read(65536)
            try:
                detail = json.loads(raw)
            except ValueError:
                detail = {}
            raise ClientError(error.code, detail.get("error", "http_error"),
                              detail.get("message", "API returned HTTP " + str(error.code))) from None

    def _json(self, path, method="GET", data=None, headers=None):
        # GET and idempotent submissions can be replayed after a lost response.
        retryable = method == "GET" or bool((headers or {}).get("Idempotency-Key"))
        for attempt in range(4):
            try:
                with self._open(path, method, data, headers) as response:
                    return json.load(response)
            except (URLError, TimeoutError, OSError):
                if not retryable or attempt == 3:
                    raise
                time.sleep(min(2 ** attempt, 8))

    def submit(self, prompts, ratio="1:1", idempotency_key=None):
        return self._json("/v1/jobs", "POST", {"prompts": prompts, "ratio": ratio},
                          {"Idempotency-Key": idempotency_key or uuid.uuid4().hex})

    def status(self, job_id, local_paths=False):
        suffix = "?local_paths=true" if local_paths and self.local else ""
        return self._json("/v1/jobs/" + quote(job_id, safe="") + suffix)

    def devices(self):
        return self._json("/v1/devices")

    def cancel(self, job_id):
        return self._json("/v1/jobs/" + quote(job_id, safe="") + "/cancel", "POST")

    def retry(self, job_id, indices=None, confirm_uncertain=False, idempotency_key=None):
        return self._json("/v1/jobs/" + quote(job_id, safe="") + "/retry", "POST",
                          {"indices": indices, "confirm_uncertain": confirm_uncertain},
                          {"Idempotency-Key": idempotency_key or uuid.uuid4().hex})

    def download(self, job_id, item, directory):
        root = Path(directory).expanduser()
        root.mkdir(parents=True, exist_ok=True)
        extension = {"image/png": ".png", "image/jpeg": ".jpg", "image/webp": ".webp"}[item["mime"]]
        target = root / (str(int(item["index"])) + extension)
        checksum, size = item["sha256"], item["size"]
        if target.exists() and target.stat().st_size == size and digest(target) == checksum:
            return target
        partial = target.with_suffix(target.suffix + ".part")
        offset = partial.stat().st_size if partial.exists() else 0
        if offset == size and digest(partial) == checksum:
            os.replace(partial, target)
            return target
        if offset >= size:
            partial.unlink()
            offset = 0
        headers = {"Range": f"bytes={offset}-"} if offset else {}
        path = f"/v1/jobs/{quote(job_id, safe='')}/images/{int(item['index'])}"
        with self._open(path, headers=headers) as response:
            append = offset > 0 and response.status == 206
            if append and not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                raise ClientError(502, "invalid_range", "Server returned an unexpected byte range.")
            with partial.open("ab" if append else "wb") as stream:
                while True:
                    block = response.read(65536)
                    if not block:
                        break
                    stream.write(block)
        if partial.stat().st_size != size or digest(partial) != checksum:
            partial.unlink(missing_ok=True)
            raise ClientError(502, "checksum_mismatch", "Downloaded image failed checksum validation; retry download.")
        os.replace(partial, target)
        return target

    def download_ready(self, job_id, directory=None, use_local_files=True, *, _seen=None):
        job = self.status(job_id, local_paths=use_local_files and directory is None)
        directory = Path(directory) if directory else Path.home() / "Downloads" / "GeminiCreatorCore" / job_id
        results = []
        for item in job["prompts"]:
            if item["status"] != "completed" or (_seen is not None and item["index"] in _seen):
                continue
            local = Path(item["path"]) if item.get("path") else None
            if local and local.is_file() and local.stat().st_size == item["size"] and digest(local) == item["sha256"]:
                path = local
            else:
                path = self.download(job_id, item, directory)
            results.append({"index": item["index"], "prompt_id": item["id"], "path": str(path)})
        return job, results

    def wait_and_download(self, job_id, directory=None, interval=1, timeout=86400):
        """Yield each completed image once; re-run with the same job ID to resume."""
        seen = set()
        started = time.monotonic()
        failures = 0
        while time.monotonic() - started < timeout:
            try:
                job, results = self.download_ready(job_id, directory, _seen=seen)
                failures = 0
            except (URLError, TimeoutError, OSError):
                failures += 1
                if failures > 8:
                    raise
                time.sleep(min(2 ** failures, 30))
                continue
            for result in results:
                if result["index"] not in seen:
                    seen.add(result["index"])
                    yield result
            if job["status"] in TERMINAL and not any(p["status"] in {"preparing", "running"} for p in job["prompts"]):
                return
            time.sleep(max(0.2, interval))
        raise TimeoutError("Job still active. Resume later with the same job_id.")

    def events(self, job_id, after=0):
        """Consume one SSE connection. Reconnect with the latest event_id to replay."""
        path = f"/v1/jobs/{quote(job_id, safe='')}/events?after={int(after)}"
        with self._open(path) as response:
            data = []
            for raw in response:
                line = raw.decode("utf-8").rstrip("\r\n")
                if line.startswith("data: "):
                    data.append(line[6:])
                elif not line and data:
                    yield json.loads("\n".join(data))
                    data.clear()
