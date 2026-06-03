"""
main.py — entry point that uses APIClient.
Run: python main.py
"""

import logging
import os

from dotenv import load_dotenv

from api_client import APIClient, APIError

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

load_dotenv()


def main():
    client = APIClient(
        base_url        = "https://jsonplaceholder.typicode.com",
        api_key         = os.environ.get("API_KEY", ""),
        rate_delay      = 0.2,
        max_retries     = 4,
        page_size       = 10,
        timeout         = 30,
        pagination_mode = "offset",
        data_key        = "data",
        cursor_key      = "next_cursor",
        total_key       = "total",
        offset_param    = "_start",
        limit_param     = "_limit",
    )

    # ── single GET ────────────────────────────────────────────────────────────
    print("\n── Single GET /posts/1 ──")
    post = client.call("GET", "/posts/1")
    print(f"  id={post['id']}  title={post['title'][:60]}")

    # ── single POST ───────────────────────────────────────────────────────────
    print("\n── POST /posts ──")
    created = client.call("POST", "/posts", data={"title": "test", "body": "hello", "userId": 1})
    print(f"  created id={created.get('id')}  title={created.get('title')}")

    # ── offset pagination ─────────────────────────────────────────────────────
    print("\n── Paginate /posts (offset, page_size=10) ──")
    total = 0
    for page_num, page in enumerate(client.paginate("/posts", mode="offset"), start=1):
        print(f"  page {page_num}: {len(page)} items  "
              f"(ids {page[0]['id']}–{page[-1]['id']})")
        total += len(page)
        if page_num >= 3:
            print("  ... stopping after 3 pages for demo")
            break
    print(f"  fetched {total} posts so far")

    # ── cursor pagination (demo only — JSONPlaceholder has no real cursor) ───
    print("\n── Paginate /posts (cursor mode, stops after page 1 — no cursor) ──")
    for page in client.paginate("/posts", mode="cursor"):
        print(f"  cursor page: {len(page)} items")
        break  # first page only in demo

    # ── error handling demo ───────────────────────────────────────────────────
    print("\n── APIError demo: GET /posts/9999 ──")
    try:
        client.call("GET", "/posts/9999")
    except APIError as e:
        print(f"  Caught APIError (status={e.status}): {e}")


if __name__ == "__main__":
    main()
