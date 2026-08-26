#!/usr/bin/env python3
"""generate_proxied.py — generate a proxied M3U playlist with only alive channels.

Reads the report.md to determine which channels are alive, then rewrites the
original M3U with all stream URLs wrapped through the Cloudflare Worker proxy.

Usage:
  python3 generate_proxied.py [--proxy URL] [--input file.m3u] [--report report.md] [--output file.m3u]
"""

import argparse
import json
import re
import sys
from base64 import b64encode
from pathlib import Path
from urllib.parse import quote

DEFAULT_PROXY = "https://dhanytv-proxy.zukron-alviandy.workers.dev"


def parse_alive_channels(report_path: str) -> set[str]:
    """Extract channel names marked as ALIVE from report.md."""
    alive = set()
    txt = Path(report_path).read_text(encoding="utf-8", errors="replace")
    for line in txt.splitlines():
        if "| ✅ ALIVE |" in line:
            parts = line.split("|")
            if len(parts) >= 5:
                name = parts[2].strip()
                alive.add(name)
    return alive


def extract_headers(lines: list[str]) -> dict:
    """Extract HTTP headers from EXTVLCOPT directives."""
    headers = {}
    for line in lines:
        if line.startswith("#EXTVLCOPT:http-referrer="):
            headers["Referer"] = line.split("=", 1)[1].strip()
        elif line.startswith("#EXTVLCOPT:http-origin="):
            headers["Origin"] = line.split("=", 1)[1].strip()
        elif line.startswith("#EXTVLCOPT:http-user-agent="):
            headers["User-Agent"] = line.split("=", 1)[1].strip()
    return headers


def proxify(url: str, headers: dict, proxy_base: str) -> str:
    """Wrap a stream URL through the proxy."""
    result = f"{proxy_base}/?url={quote(url, safe='')}"
    if headers:
        h_json = json.dumps(headers, separators=(",", ":"))
        h_b64 = b64encode(h_json.encode()).decode()
        result += f"&h={quote(h_b64, safe='')}"
    return result


def generate_proxied_m3u(input_path: str, report_path: str, proxy_base: str) -> str:
    """Generate proxied M3U with only alive channels."""
    alive = parse_alive_channels(report_path)
    if not alive:
        print(f"Warning: no alive channels found in {report_path}", file=sys.stderr)

    txt = Path(input_path).read_text(encoding="utf-8", errors="replace")
    blocks = re.split(r"(?=^#EXTINF)", txt, flags=re.M)

    output_blocks = []
    header = '#EXTM3U url-tvg="https://raw.githubusercontent.com/dhasap/dhanytv/main/epg.xml"'
    skipped = 0
    included = 0

    for block in blocks:
        if not block.startswith("#EXTINF"):
            continue

        lines = block.strip().splitlines()
        name_match = re.search(r",(.+)$", lines[0])
        name = name_match.group(1).strip() if name_match else ""

        if alive and name not in alive:
            skipped += 1
            continue

        url = ""
        url_idx = -1
        for i, line in enumerate(lines[1:], 1):
            if line.startswith("http") or line.startswith("https"):
                url = line.split("|", 1)[0].strip()
                url_idx = i
                break

        if not url:
            skipped += 1
            continue

        headers = extract_headers(lines)
        proxied_url = proxify(url, headers, proxy_base)

        new_lines = [lines[0]]
        for i, line in enumerate(lines[1:], 1):
            if i == url_idx:
                new_lines.append(proxied_url)
            elif line.startswith("#EXTVLCOPT:") or line.startswith("#KODIPROP:") or line.startswith("#EXTHTTP:"):
                continue
            else:
                new_lines.append(line)

        output_blocks.append("\n".join(new_lines))
        included += 1

    print(f"Included: {included} channels, Skipped: {skipped}", file=sys.stderr)
    return header + "\n\n" + "\n\n".join(output_blocks) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Generate proxied M3U playlist from alive channels")
    parser.add_argument("--proxy", default=DEFAULT_PROXY, help=f"Proxy base URL (default: {DEFAULT_PROXY})")
    parser.add_argument("--input", default=str(Path(__file__).parent.parent / "dhanytv-ott.m3u"),
                        help="Source M3U file (default: ../dhanytv-ott.m3u)")
    parser.add_argument("--report", default=str(Path(__file__).parent.parent / "report.md"),
                        help="Report file from check_streams.py (default: ../report.md)")
    parser.add_argument("--output", default=str(Path(__file__).parent.parent / "dhanytv-proxied.m3u"),
                        help="Output proxied M3U file (default: ../dhanytv-proxied.m3u)")
    args = parser.parse_args()

    if not Path(args.input).exists():
        print(f"Error: input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)
    if not Path(args.report).exists():
        print(f"Error: report file not found: {args.report}", file=sys.stderr)
        sys.exit(1)

    result = generate_proxied_m3u(args.input, args.report, args.proxy)
    Path(args.output).write_text(result, encoding="utf-8")
    print(f"Written to: {args.output}", file=sys.stderr)


if __name__ == "__main__":
    main()
