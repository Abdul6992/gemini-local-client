import argparse
import json
import os
from pathlib import Path
from .client import Client


def main():
    parser = argparse.ArgumentParser(description="Generate/download images from a local or LAN desktop.")
    parser.add_argument("--url", default=os.environ.get("GEMINI_LOCAL_URL", "http://127.0.0.1:8765"))
    parser.add_argument("--certificate", default=os.environ.get("GEMINI_LOCAL_CERT"))
    parser.add_argument("--prompts", help="UTF-8 text file; one prompt per line")
    parser.add_argument("--ratio", choices=["1:1", "16:9", "9:16"], default="1:1")
    parser.add_argument("--resume", help="Existing job_id to resume downloading")
    parser.add_argument("--output", help="Download directory")
    parser.add_argument("--idempotency-key", help="Reuse this value if resubmitting the same prompts")
    args = parser.parse_args()
    if not args.resume and not args.prompts:
        parser.error("Provide --prompts or --resume.")
    client = Client(args.url, certificate=args.certificate)
    if args.resume:
        ident = args.resume
    else:
        prompts = [p.strip() for p in Path(args.prompts).read_text(encoding="utf-8-sig").splitlines() if p.strip()]
        ident = client.submit(prompts, args.ratio, args.idempotency_key)["job_id"]
    print(json.dumps({"job_id": ident}), flush=True)
    for image in client.wait_and_download(ident, args.output):
        print(json.dumps(image), flush=True)
    status = client.status(ident)
    print(json.dumps({"job_id": ident, "status": status["status"],
                      "completed_indices": status["completed_indices"],
                      "failed_indices": status["failed_indices"]}), flush=True)
    if status["status"] != "completed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
