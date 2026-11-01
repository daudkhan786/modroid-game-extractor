import json
import re
import time
import html as html_lib
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen
import xml.etree.ElementTree as ET

BASE = "https://moddroid.com"
HEADERS = {"User-Agent": "Mozilla/5.0"}
OUTPUT = "games-metadata.txt"
DELAY = 1

CATEGORIES = {
    "action", "adventure", "arcade", "board", "card", "casual",
    "educational", "puzzle", "racing", "rpg", "simulation",
    "sports", "strategy", "music"
}


def fetch(url):
    req = Request(url, headers=HEADERS)
    with urlopen(req, timeout=30) as r:
        return r.read()


def clean(value):
    value = html_lib.unescape(value or "")
    return re.sub(r"\s+", " ", value).strip()


def get_sitemaps():
    text = fetch(BASE + "/robots.txt").decode("utf-8", "ignore")
    return re.findall(r"(?im)^Sitemap:\s*(\S+)", text)


def read_sitemap(url, seen=None):
    if seen is None:
        seen = set()
    if url in seen:
        return []
    seen.add(url)

    try:
        root = ET.fromstring(fetch(url))
    except Exception as e:
        print("Sitemap error:", url, e)
        return []

    result = []
    for node in root:
        loc = next(
            (x.text for x in node if x.tag.endswith("loc")), None
        )
        if not loc:
            continue
        if node.tag.endswith("sitemap"):
            result.extend(read_sitemap(loc, seen))
        elif node.tag.endswith("url"):
            result.append(loc)
    return result


def is_game_page(url):
    p = urlparse(url)
    if p.netloc.lower() not in ("moddroid.com", "www.moddroid.com"):
        return False

    parts = [x for x in p.path.strip("/").split("/") if x]
    return (
        len(parts) == 3
        and parts[0] == "games"
        and parts[1].lower() in CATEGORIES
    )


def get_meta(doc, key, attr="property"):
    pattern = (
        r"<meta\b(?=[^>]*\b" + attr + r"\s*=\s*['\"]"
        + re.escape(key) +
        r"['\"])[^>]*\bcontent\s*=\s*['\"]([^'\"]*)['\"][^>]*>"
    )
    m = re.search(pattern, doc, re.I)
    if m:
        return clean(m.group(1))

    pattern = (
        r"<meta\b(?=[^>]*\bcontent\s*=\s*['\"]([^'\"]*)['\"])"
        r"(?=[^>]*\b" + attr + r"\s*=\s*['\"]"
        + re.escape(key) + r"['\"])[^>]*>"
    )
    m = re.search(pattern, doc, re.I)
    return clean(m.group(1)) if m else ""


def structured_data(doc):
    objects = []
    for raw in re.findall(
        r"<script\b[^>]*type=['\"]application/ld\+json['\"][^>]*>(.*?)</script>",
        doc, re.I | re.S
    ):
        try:
            data = json.loads(raw.strip())
            if isinstance(data, list):
                objects.extend(data)
            elif isinstance(data, dict):
                objects.extend(data.get("@graph", [data]))
        except Exception:
            pass
    return objects


def extract(url):
    doc = fetch(url).decode("utf-8", "replace")
    data = structured_data(doc)

    # Prefer structured data belonging to the current page.
    page_obj = next(
        (x for x in data if isinstance(x, dict)
         and x.get("@type") in ("SoftwareApplication", "VideoGame", "Product")),
        {}
    )

    name = (
        clean(page_obj.get("name"))
        or get_meta(doc, "og:title")
        or get_meta(doc, "twitter:title", "name")
    )
    description = (
        clean(page_obj.get("description"))
        or get_meta(doc, "og:description")
        or get_meta(doc, "description", "name")
    )

    # Avoid using category/list-page titles as app names.
    if not name or name.lower() in ("games", "action", "adventure", "arcade"):
        title_match = re.search(r"<title[^>]*>(.*?)</title>", doc, re.I | re.S)
        name = clean(title_match.group(1)) if title_match else ""

    icon = (
        clean(page_obj.get("image"))
        if isinstance(page_obj.get("image"), str) else ""
    )
    if isinstance(page_obj.get("image"), dict):
        icon = clean(page_obj["image"].get("url"))
    if not icon:
        icon = get_meta(doc, "og:image") or get_meta(doc, "twitter:image", "name")
    if icon:
        icon = urljoin(url, icon)

    package = clean(page_obj.get("applicationCategory", ""))
    package_match = re.search(
        r'(?i)(?:packageName|package_name|package)\s*["\']?\s*[:=]\s*["\']'
        r'([a-zA-Z0-9_]+(?:\.[a-zA-Z0-9_]+)+)', doc
    )
    if package_match:
        package = package_match.group(1)
    else:
        package = ""

    version = clean(page_obj.get("softwareVersion", ""))
    if not version:
        for pattern in (
            r'"softwareVersion"\s*:\s*"([^"]+)"',
            r'"version"\s*:\s*"([^"]+)"',
        ):
            m = re.search(pattern, doc, re.I)
            if m:
                version = clean(m.group(1))
                break

    size = ""
    m = re.search(r'"fileSize"\s*:\s*"([^"]+)"', doc, re.I)
    if m:
        size = clean(m.group(1))
    else:
        m = re.search(r'\b(?:APK\s*)?Size\b\s*[:<][^>]{0,80}?(\d+(?:\.\d+)?\s*(?:MB|GB|KB))', doc, re.I)
        if m:
            size = clean(m.group(1))

    developer = ""
    author = page_obj.get("author", "")
    if isinstance(author, dict):
        developer = clean(author.get("name", ""))
    elif isinstance(author, str):
        developer = clean(author)

    # Collect image candidates from image tags, lazy loading and srcset.
    candidates = []
    for tag in re.findall(r"<img\b[^>]*>", doc, re.I):
        attrs = dict(re.findall(
            r'([\w:-]+)\s*=\s*["\']([^"\']*)["\']', tag, re.I
        ))
        alt = clean(attrs.get("alt", "")).lower()
        context = (tag + " " + alt).lower()

        if any(x in context for x in ("logo", "avatar", "author", "banner", "icon")):
            continue

        src = (
            attrs.get("data-src") or attrs.get("data-lazy-src")
            or attrs.get("src") or ""
        )
        if src.startswith("data:"):
            src = ""

        srcset = attrs.get("srcset", "")
        if not src and srcset:
            src = srcset.split(",")[0].strip().split(" ")[0]

        if src:
            full = urljoin(url, html_lib.unescape(src))
            if urlparse(full).scheme in ("http", "https") and full != icon:
                candidates.append(full)

    # Prefer gallery/screenshot-labelled images, otherwise use page images
    # excluding obvious thumbnails. The final 3 still need manual verification.
    gallery = []
    for tag in re.findall(r"<img\b[^>]*>", doc, re.I):
        attrs = dict(re.findall(
            r'([\w:-]+)\s*=\s*["\']([^"\']*)["\']', tag, re.I
        ))
        src = attrs.get("data-src") or attrs.get("src") or ""
        alt = attrs.get("alt", "").lower()
        context = (tag + " " + alt).lower()
        if src and any(x in context for x in ("screenshot", "screen-shot", "gallery", "preview")):
            full = urljoin(url, html_lib.unescape(src))
            if full != icon and full.startswith("http"):
                gallery.append(full)

    screenshots = list(dict.fromkeys(gallery or candidates))[:3]

    return {
        "App Name": name,
        "Package Name": package,
        "Developer": developer,
        "Category": "Games",
        "Version Name": version,
        "Version Code": "",
        "Size (MB)": size,
        "Icon URL": icon,
        "Screenshots URLs": screenshots,
        "Description": description,
        "App URL": url,
    }


def main():
    urls = set()
    for sm in get_sitemaps():
        urls.update(read_sitemap(sm))

    games = sorted(u for u in urls if is_game_page(u))
    print("Individual game pages found:", len(games))

    records = []
    for i, url in enumerate(games, 1):
        try:
            item = extract(url)
            records.append(item)
            print(f"{i}/{len(games)}: {item['App Name'] or url}")
        except Exception as e:
            print("Failed:", url, e)
        time.sleep(DELAY)

    with open(OUTPUT, "w", encoding="utf-8") as f:
        for item in records:
            f.write("=" * 50 + "\n")
            for key, value in item.items():
                if key == "Screenshots URLs":
                    f.write(key + ":\n")
                    for link in value:
                        f.write("  " + link + "\n")
                else:
                    f.write(f"{key}: {value}\n")
            f.write("\n")

    print("Saved records:", len(records))


if __name__ == "__main__":
    main()
