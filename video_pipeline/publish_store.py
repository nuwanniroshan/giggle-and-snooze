"""Publish a built book to the public store's Supabase project (spec 029, story S8).

Uploads the EPUB (private bucket) and a JPEG cover (public bucket), then upserts the catalog
row. A new book is a draft until `--publish` is passed. Reads SUPABASE_URL and
SUPABASE_SERVICE_ROLE_KEY from the environment.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import sys
import urllib.request
from pathlib import Path

from PIL import Image

from video_pipeline.library import PROJECT_ROOT, book_folder, file_stem

ACCESS_LEVELS = ("free", "members", "paid")


def build_catalog_row(
    book: dict,
    slug: str,
    *,
    access: str,
    price_cents: int | None,
    publish: bool,
    epub_path: str,
    cover_path: str,
) -> dict:
    """The `books` row for a book. Pure: no files, no network."""
    if access not in ACCESS_LEVELS:
        raise ValueError(f"access must be one of {ACCESS_LEVELS}, got {access!r}")
    if access == "paid" and price_cents is None:
        raise ValueError("a paid book needs --price-cents")
    meta = book["metadata"]
    row = {
        "slug": slug,
        "title": meta["title"],
        "subtitle": meta.get("subtitle"),
        "blurb": meta.get("blurb"),
        "access": access,
        "price_cents": price_cents if access == "paid" else None,
        "page_count": meta.get("digital_page_count"),
        "epub_path": epub_path,
        "cover_path": cover_path,
        "published": publish,
    }
    return row


def _request(method: str, url: str, key: str, body: bytes, headers: dict[str, str]) -> None:
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("apikey", key)
    req.add_header("Authorization", f"Bearer {key}")
    for name, value in headers.items():
        req.add_header(name, value)
    with urllib.request.urlopen(req, timeout=300) as resp:  # noqa: S310 (https/http URL from env)
        resp.read()


def _upload(base: str, key: str, bucket: str, path: str, data: bytes, content_type: str) -> None:
    _request(
        "POST",
        f"{base}/storage/v1/object/{bucket}/{path}",
        key,
        data,
        {"Content-Type": content_type, "x-upsert": "true"},
    )


def _cover_jpeg(png: Path) -> bytes:
    with Image.open(png) as im:
        rgb = im.convert("RGB")
        rgb.thumbnail((1200, 1200), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        rgb.save(out, "JPEG", quality=88)
        return out.getvalue()


def main(argv: list[str] | None = None) -> int:
    from datetime import UTC, datetime

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--book", required=True)
    parser.add_argument("--access", choices=ACCESS_LEVELS, default="paid")
    parser.add_argument("--price-cents", type=int)
    parser.add_argument("--publish", action="store_true", help="make the book visible in the store")
    parser.add_argument("--root", type=Path, default=PROJECT_ROOT)
    args = parser.parse_args(argv)

    base = os.environ.get("SUPABASE_URL", "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not base or not key:
        print("Set SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY.", file=sys.stderr)
        return 2

    folder = book_folder(args.book, args.root)
    book = json.loads(folder.book_json.read_text(encoding="utf-8"))
    stem = file_stem(book["metadata"]["title"])
    epub = folder.build_dir / f"{stem}.epub"
    cover = folder.build_dir / f"{stem}_Front_Cover.png"
    for needed in (epub, cover):
        if not needed.is_file():
            print(f"Missing {needed}. Run build_book.py first.", file=sys.stderr)
            return 1

    epub_path = f"{args.book}/{args.book}.epub"
    cover_path = f"{args.book}/cover.jpg"
    _upload(base, key, "epubs", epub_path, epub.read_bytes(), "application/epub+zip")
    _upload(base, key, "covers", cover_path, _cover_jpeg(cover), "image/jpeg")

    row = build_catalog_row(
        book,
        args.book,
        access=args.access,
        price_cents=args.price_cents,
        publish=args.publish,
        epub_path=epub_path,
        cover_path=cover_path,
    )
    if args.publish:
        row["published_at"] = datetime.now(UTC).isoformat()
    _request(
        "POST",
        f"{base}/rest/v1/books?on_conflict=slug",
        key,
        json.dumps(row).encode(),
        {"Content-Type": "application/json", "Prefer": "resolution=merge-duplicates"},
    )
    state = "published" if args.publish else "saved as draft"
    print(f"{args.book}: {state} ({args.access})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
