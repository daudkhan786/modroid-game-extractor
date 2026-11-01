import json
import re
import time
import urllib.robotparser
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

BASE = "https://moddroid.com"
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; PublicMetadataReader/1.0)"}
MAX_PAGES = 100
DELAY = 1
OUTPUT = "games-metadata.txt"


def fetch(url):
    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=25) as response:
        return response.read()


def get_sitemaps():
    robots = fetch(BASE + "/robots.txt").decode("utf-8", "ignore")
    maps = re.findall(r"(?im)^Sitemap:\s*(\S+)", robots)
    return maps or [BASE + "/sitemap.xml"]


def sitemap_urls(url, seen=None):
    if seen is None:
        seen = set()
    if url in seen:
        return []
    seen.add(url)
    try:
        root = ET.fromstring(fetch(url))
    except Exception as exc:
        print("Sitemap skipped:", url, exc)
        return []

    found = []
    for node in root:
        loc = next((x.text for x in node if x.tag.endswith("loc")), None)
        if not loc:
            continue
        if node.tag.endswith("sitemap"):
            found.extend(sitemap_urls(loc, seen))
        elif node.tag.endswith("url"):
            found.append(loc)
    return found


def meta(html, *names):
    for name in names:
        patterns = [
            rf'<meta[^>]+(?:property|name)=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']*)',
            rf'<meta[^>]+content=["\']([^"\']*)["\'][^>]+(?:property|name)=["\']{re.escape(name)}["\']',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.I)
            if match:
                return match.group(1).strip()
    return ""


def extract(url):
    html = fetch(url).decode("utf-8", "replace")
    title = meta(html, "og:title", "twitter:title")
    description = meta(html, "og:description", "description", "twitter:description")
    icon = meta(html, "og:image", "twitter:image")
    if icon:
        icon = urljoin(url, icon)

    package = ""
    patterns = [
        r'com\.[a-zA-Z0-9_.]+',
        r'package(?:Name|_name)?["\']?\s*[:=]\s*["\']([a-zA-Z0-9_.]+)',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, re.I)
        if match:
            package = match.group(1) if match.lastindex else match.group(0)
            break

    version = ""
    for pattern in [
        r'"softwareVersion"\s*:\s*"([^"]+)',
        r'"version"\s*:\s*"([^"]+)',
        r'\bVersion\b\s*</[^>]+>\s*<[^>]+>\s*([^<]+)',
    ]:
        match = re.search(pattern, html, re.I)
        if match:
            version = match.group(1).strip()
            break

    size = ""
    match = re.search(r'(\d+(?:\.\d+)?)\s*(MB|GB|KB)\b', html, re.I)
    if match:
        size = match.group(1) + " " + match.group(2).upper()

    screenshots = []
    for src in re.findall(
        r'<img\b[^>]*?(?:src|data-src)=["\']([^"\']+)["\'][^>]*>',
        html, re.I
    ):
        full = urljoin(url, src)
        if full != icon and urlparse(full).scheme in ("http", "https"):
            if any(word in full.lower() for word in ("screen", "screenshot", "preview", "gallery")):
                screenshots.append(full)

    developer = ""
    match = re.search(r'"author"\s*:\s*\{[^}]*"name"\s*:\s*"([^"]+)', html, re.I)
    if match:
        developer = match.group(1)

    return {
        "App Name": re.sub(r"\s+", " ", title).strip(),
        "Package Name": package,
        "Developer": developer,
        "Category": "Games",
        "Version Name": version,
        "Version Code": "",
        "Size (MB)": size,
        "Icon URL": icon,
        "Screenshots URLs": list(dict.fromkeys(screenshots)),
        "Description": description,
        "App URL": url,
    }


def main():
    urls = set()
    for sm in get_sitemaps():
        urls.update(sitemap_urls(sm))

    game_urls = sorted(
        u for u in urls
        if urlparse(u).netloc.endswith("moddroid.com")
        and "/games/" in urlparse(u).path
        and u.rstrip("/") != BASE + "/games"
    )
    print("Game pages found:", len(game_urls))

    results = []
    for index, url in enumerate(game_urls[:MAX_PAGES], 1):
        try:
            item = extract(url)
            results.append(item)
            print(f"[{index}/{min(len(game_urls), MAX_PAGES)}] {item['App Name'] or url}")
        except Exception as exc:
            print("Page skipped:", url, exc)
        time.sleep(DELAY)

    with open(OUTPUT, "w", encoding="utf-8") as file:
        for item in results:
            file.write("=" * 50 + "\n")
            for key, value in item.items():
                if isinstance(value, list):
                    file.write(key + ":\n")
                    for entry in value:
                        file.write("  " + entry + "\n")
                else:
                    file.write(f"{key}: {value}\n")
            file.write("\n")

    print("Saved", len(results), "records to", OUTPUT)


if __name__ == "__main__":
    main()
