import os, re, sys, urllib.request, html, json, time

# 需要代理时：export SINGBOX_DOCS_PROXY=http://127.0.0.1:7890；默认直连
PROXY = os.environ.get("SINGBOX_DOCS_PROXY", "")
BASE = "https://sing-box.sagernet.org/"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs")

PAGES = [
    "configuration/index/",
    "configuration/inbound/",
    "configuration/inbound/tun/",
    "configuration/inbound/mixed/",
    "configuration/outbound/",
    "configuration/outbound/anytls/",
    "configuration/outbound/selector/",
    "configuration/outbound/urltest/",
    "configuration/dns/",
    "configuration/dns/server/",
    "configuration/dns/rule/",
    "configuration/route/",
    "configuration/route/rule/",
    "configuration/route/rule_action/",
    "configuration/rule-set/",
    "configuration/rule-set/headless-rule/",
    "migration/",
]

opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({"http": PROXY, "https": PROXY}) if PROXY else urllib.request.ProxyHandler({})
)
opener.addheaders = [("User-Agent", "Mozilla/5.0")]

TAG_RE = re.compile(r"<[^>]+>")
SCRIPT_RE = re.compile(r"<(script|style|nav|svg)\b.*?</\1>", re.S | re.I)


def to_text(h):
    # keep only the main article if we can find it
    m = re.search(r"<article\b.*?</article>", h, re.S | re.I)
    if m:
        h = m.group(0)
    h = SCRIPT_RE.sub(" ", h)
    h = re.sub(r"<(br|/p|/div|/tr|/li|/h[1-6]|/pre)\b[^>]*>", "\n", h, flags=re.I)
    h = re.sub(r"</t[dh]>", " | ", h, flags=re.I)
    h = TAG_RE.sub("", h)
    h = html.unescape(h)
    h = re.sub(r"[ \t]+", " ", h)
    h = re.sub(r"\n\s*\n\s*\n+", "\n\n", h)
    return "\n".join(l.rstrip() for l in h.splitlines()).strip()


os.makedirs(OUT, exist_ok=True)
index = []
for p in PAGES:
    url = BASE + p
    name = p.strip("/").replace("/", "__") + ".txt"
    try:
        with opener.open(url, timeout=30) as r:
            raw = r.read().decode("utf-8", "replace")
        txt = to_text(raw)
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as f:
            f.write(txt)
        index.append((p, len(txt), "ok"))
    except Exception as e:
        index.append((p, 0, "ERR " + str(e)[:80]))
    time.sleep(0.3)

for p, n, s in index:
    print(f"{s:12} {n:7} {p}")
