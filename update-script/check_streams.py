#!/usr/bin/env python3
"""check_streams.py — check which stream URLs in an M3U playlist are alive.

Usage:
  python3 check_streams.py [file.m3u] [--timeout 10] [--workers 16] [--group "Sports"]
"""

import argparse
import concurrent.futures as cf
import re
import ssl
import sys
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0 Safari/537.36"
CTX = ssl.create_default_context()
CTX.check_hostname = False
CTX.verify_mode = ssl.CERT_NONE

STATUS_ALIVE = "ALIVE"
STATUS_DEAD = "DEAD"
STATUS_TIMEOUT = "TIMEOUT"
STATUS_ERROR = "ERROR"


@dataclass
class Channel:
    name: str
    group: str
    url: str
    referrer: str = ""
    origin: str = ""
    user_agent: str = ""


def parse_m3u(path: str, group_filter: str | None = None) -> list[Channel]:
    txt = Path(path).read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"(?=^#EXTINF)", txt, flags=re.M)
    channels = []

    for block in blocks:
        if not block.startswith("#EXTINF"):
            continue

        lines = block.strip().splitlines()

        name_match = re.search(r",(.+)$", lines[0])
        name = name_match.group(1).strip() if name_match else "Unknown"

        group_match = re.search(r'group-title="([^"]*)"', lines[0])
        group = group_match.group(1) if group_match else ""

        if group_filter and group.lower() != group_filter.lower():
            continue

        url = ""
        referrer = ""
        origin = ""
        user_agent = ""

        for line in lines[1:]:
            if line.startswith("http") and not url:
                url = line.split("|", 1)[0].strip()
            elif line.startswith("#EXTVLCOPT:http-referrer="):
                referrer = line.split("=", 1)[1].strip()
            elif line.startswith("#EXTVLCOPT:http-origin="):
                origin = line.split("=", 1)[1].strip()
            elif line.startswith("#EXTVLCOPT:http-user-agent="):
                user_agent = line.split("=", 1)[1].strip()

        if not url:
            continue

        channels.append(Channel(
            name=name, group=group, url=url,
            referrer=referrer, origin=origin, user_agent=user_agent,
        ))

    return channels


def is_valid_stream_content(data: bytes, url: str) -> bool:
    """Check if response body looks like actual stream data."""
    if not data:
        return False
    text = data.decode("utf-8", errors="replace")[:2048]
    if url.endswith(".m3u8") or ".m3u8" in url:
        return "#EXTM3U" in text or "#EXTINF" in text or "#EXT-X-" in text
    if url.endswith(".mpd") or ".mpd" in url:
        return "<MPD" in text or "urn:mpeg:dash" in text
    # For generic URLs, accept binary (likely TS segments) or valid manifest
    if text.startswith("#EXTM3U") or "<MPD" in text:
        return True
    # Binary data (TS segment) — first bytes are 0x47 (MPEG-TS sync byte)
    if len(data) > 10 and data[0:1] == b"\x47":
        return True
    # Non-empty response with media content-type is acceptable
    if len(data) > 100:
        return True
    return False


def check_stream(channel: Channel, timeout: int) -> tuple[Channel, str, str]:
    headers = {"User-Agent": channel.user_agent or UA}
    if channel.referrer:
        headers["Referer"] = channel.referrer
    if channel.origin:
        headers["Origin"] = channel.origin
    headers["Range"] = "bytes=0-1023"

    try:
        req = urllib.request.Request(channel.url, headers=headers)
        resp = urllib.request.urlopen(req, timeout=timeout, context=CTX)
        code = resp.getcode()
        data = resp.read(2048)
        resp.close()
        if code in (200, 206):
            if not is_valid_stream_content(data, channel.url):
                return channel, STATUS_DEAD, f"{code} (empty/invalid content)"
            return channel, STATUS_ALIVE, str(code)
        return channel, STATUS_DEAD, str(code)
    except HTTPError as e:
        return channel, STATUS_DEAD, str(e.code)
    except URLError as e:
        reason = str(e.reason)
        if "timed out" in reason or "timeout" in reason.lower():
            return channel, STATUS_TIMEOUT, reason
        return channel, STATUS_ERROR, reason
    except TimeoutError:
        return channel, STATUS_TIMEOUT, "connection timed out"
    except Exception as e:
        return channel, STATUS_ERROR, str(e)


def write_markdown(path: str, results: dict, total: int, alive: int, dead: int,
                   timeout: int, error: int, pct: float):
    lines = []
    lines.append("# Stream Check Report\n")
    lines.append(f"| Metric | Count |")
    lines.append(f"|--------|-------|")
    lines.append(f"| Total | {total} |")
    lines.append(f"| Alive | {alive} ({pct:.1f}%) |")
    lines.append(f"| Dead | {dead} |")
    lines.append(f"| Timeout | {timeout} |")
    lines.append(f"| Error | {error} |")
    lines.append("")

    all_results = []
    for ch, detail in results[STATUS_ALIVE]:
        all_results.append((ch, STATUS_ALIVE, detail))
    for ch, detail in results[STATUS_DEAD]:
        all_results.append((ch, STATUS_DEAD, detail))
    for ch, detail in results[STATUS_TIMEOUT]:
        all_results.append((ch, STATUS_TIMEOUT, detail))
    for ch, detail in results[STATUS_ERROR]:
        all_results.append((ch, STATUS_ERROR, detail))

    all_results.sort(key=lambda x: (x[1] != STATUS_DEAD, x[1] != STATUS_TIMEOUT,
                                     x[1] != STATUS_ERROR, x[0].group, x[0].name))

    lines.append("## Results\n")
    lines.append("| # | Channel | Group | Status | Detail |")
    lines.append("|---|---------|-------|--------|--------|")
    for i, (ch, status, detail) in enumerate(all_results, 1):
        status_icon = {"ALIVE": "✅", "DEAD": "❌", "TIMEOUT": "⏱️", "ERROR": "⚠️"}[status]
        detail_escaped = detail.replace("|", "\\|")[:60]
        lines.append(f"| {i} | {ch.name} | {ch.group} | {status_icon} {status} | {detail_escaped} |")

    Path(path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nMarkdown report written to: {path}")


def main():
    parser = argparse.ArgumentParser(description="Check M3U stream URLs for availability")
    parser.add_argument("file", nargs="?", default=str(Path(__file__).parent.parent / "dhanytv-ott.m3u"),
                        help="M3U file to check (default: ../dhanytv-ott.m3u)")
    parser.add_argument("--timeout", type=int, default=10, help="HTTP timeout in seconds (default: 10)")
    parser.add_argument("--workers", type=int, default=16, help="Concurrent workers (default: 16)")
    parser.add_argument("--group", type=str, default=None, help="Only check channels in this group-title")
    parser.add_argument("--markdown", type=str, default=None, metavar="FILE",
                        help="Write results as a markdown table to FILE (e.g. report.md)")
    parser.add_argument("--url", type=str, default=None,
                        help="Check a single URL instead of the whole playlist")
    args = parser.parse_args()

    if args.url:
        ch = Channel(name="Single URL", group="", url=args.url)
        _, status, detail = check_stream(ch, args.timeout)
        icon = {"ALIVE": "✅", "DEAD": "❌", "TIMEOUT": "⏱️", "ERROR": "⚠️"}[status]
        print(f"{icon} {status} ({detail})")
        print(f"   {args.url}")
        sys.exit(0 if status == STATUS_ALIVE else 1)

    if not Path(args.file).exists():
        print(f"Error: file not found: {args.file}", file=sys.stderr)
        sys.exit(1)

    channels = parse_m3u(args.file, args.group)
    if not channels:
        print("No channels found to check.")
        sys.exit(0)

    print(f"Checking {len(channels)} streams ({args.workers} workers, {args.timeout}s timeout)...\n")

    results = {STATUS_ALIVE: [], STATUS_DEAD: [], STATUS_TIMEOUT: [], STATUS_ERROR: []}

    with cf.ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(check_stream, ch, args.timeout): ch for ch in channels}
        done_count = 0
        for fut in cf.as_completed(futures):
            done_count += 1
            channel, status, detail = fut.result()
            results[status].append((channel, detail))

            if status != STATUS_ALIVE:
                print(f"[{status} {detail}] {channel.name}")
                print(f"  URL: {channel.url}")
                if channel.group:
                    print(f"  Group: {channel.group}")
                print()

            if done_count % 50 == 0:
                print(f"  ... {done_count}/{len(channels)} checked", file=sys.stderr)

    alive = len(results[STATUS_ALIVE])
    dead = len(results[STATUS_DEAD])
    timeout = len(results[STATUS_TIMEOUT])
    error = len(results[STATUS_ERROR])
    total = len(channels)
    pct = (alive / total * 100) if total else 0

    print(f"\n{'═' * 40}")
    print(f"  Summary")
    print(f"{'═' * 40}")
    print(f"  Total:   {total}")
    print(f"  Alive:   {alive} ({pct:.1f}%)")
    print(f"  Dead:    {dead}")
    print(f"  Timeout: {timeout}")
    print(f"  Error:   {error}")
    print(f"{'═' * 40}")

    if dead > 0:
        print(f"\n  Dead URLs:")
        for ch, detail in results[STATUS_DEAD]:
            print(f"    [{detail}] {ch.name} — {ch.url}")

    if timeout > 0:
        print(f"\n  Timed-out URLs:")
        for ch, detail in results[STATUS_TIMEOUT]:
            print(f"    {ch.name} — {ch.url}")

    if args.markdown:
        write_markdown(args.markdown, results, total, alive, dead, timeout, error, pct)


if __name__ == "__main__":
    main()
