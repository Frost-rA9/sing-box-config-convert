"""Region tables: a Chinese name, a flag emoji, or a two-letter code maps to a region code.

Region codes use ISO 3166-1 alpha-2.
"""

from __future__ import annotations

import re

# Chinese name to region code. A single character such as "台" matches too many
# node names, so use multi-character words only.
CHINESE_NAMES: dict[str, str] = {
    "香港": "HK",
    "台湾": "TW",
    "臺灣": "TW",
    "台北": "TW",
    "日本": "JP",
    "东京": "JP",
    "大阪": "JP",
    "新加坡": "SG",
    "狮城": "SG",
    "美国": "US",
    "美國": "US",
    "洛杉矶": "US",
    "圣何塞": "US",
    "西雅图": "US",
    "达拉斯": "US",
    "纽约": "US",
    "芝加哥": "US",
    "韩国": "KR",
    "首尔": "KR",
    "英国": "GB",
    "伦敦": "GB",
    "德国": "DE",
    "法兰克福": "DE",
    "法国": "FR",
    "巴黎": "FR",
    "荷兰": "NL",
    "加拿大": "CA",
    "澳大利亚": "AU",
    "澳洲": "AU",
    "新西兰": "NZ",
    "印度": "IN",
    "印尼": "ID",
    "印度尼西亚": "ID",
    "泰国": "TH",
    "越南": "VN",
    "马来西亚": "MY",
    "菲律宾": "PH",
    "土耳其": "TR",
    "西班牙": "ES",
    "意大利": "IT",
    "波兰": "PL",
    "俄罗斯": "RU",
    "瑞士": "CH",
    "瑞典": "SE",
    "挪威": "NO",
    "芬兰": "FI",
    "丹麦": "DK",
    "奥地利": "AT",
    "比利时": "BE",
    "爱尔兰": "IE",
    "葡萄牙": "PT",
    "捷克": "CZ",
    "罗马尼亚": "RO",
    "保加利亚": "BG",
    "希腊": "GR",
    "匈牙利": "HU",
    "乌克兰": "UA",
    "摩尔多瓦": "MD",
    "以色列": "IL",
    "阿联酋": "AE",
    "迪拜": "AE",
    "沙特": "SA",
    "巴基斯坦": "PK",
    "孟加拉": "BD",
    "斯里兰卡": "LK",
    "尼泊尔": "NP",
    "哈萨克": "KZ",
    "蒙古": "MN",
    "南非": "ZA",
    "埃及": "EG",
    "尼日利亚": "NG",
    "墨西哥": "MX",
    "巴西": "BR",
    "阿根廷": "AR",
    "智利": "CL",
    "秘鲁": "PE",
    "哥伦比亚": "CO",
    "中国大陆": "CN",
    "中国": "CN",
    "回国": "CN",
    "澳门": "MO",
}

# Code to Chinese display name, used by the report.
REGION_NAMES: dict[str, str] = {
    "HK": "香港",
    "TW": "台湾",
    "JP": "日本",
    "SG": "新加坡",
    "US": "美国",
    "KR": "韩国",
    "GB": "英国",
    "DE": "德国",
    "FR": "法国",
    "NL": "荷兰",
    "CA": "加拿大",
    "AU": "澳大利亚",
    "NZ": "新西兰",
    "IN": "印度",
    "ID": "印尼",
    "TH": "泰国",
    "VN": "越南",
    "MY": "马来西亚",
    "PH": "菲律宾",
    "TR": "土耳其",
    "ES": "西班牙",
    "IT": "意大利",
    "PL": "波兰",
    "RU": "俄罗斯",
    "CH": "瑞士",
    "SE": "瑞典",
    "NO": "挪威",
    "FI": "芬兰",
    "DK": "丹麦",
    "AT": "奥地利",
    "BE": "比利时",
    "IE": "爱尔兰",
    "PT": "葡萄牙",
    "CZ": "捷克",
    "RO": "罗马尼亚",
    "BG": "保加利亚",
    "GR": "希腊",
    "HU": "匈牙利",
    "UA": "乌克兰",
    "MD": "摩尔多瓦",
    "IL": "以色列",
    "AE": "阿联酋",
    "SA": "沙特",
    "PK": "巴基斯坦",
    "BD": "孟加拉",
    "LK": "斯里兰卡",
    "NP": "尼泊尔",
    "KZ": "哈萨克",
    "MN": "蒙古",
    "ZA": "南非",
    "EG": "埃及",
    "NG": "尼日利亚",
    "MX": "墨西哥",
    "BR": "巴西",
    "AR": "阿根廷",
    "CL": "智利",
    "PE": "秘鲁",
    "CO": "哥伦比亚",
    "CN": "中国大陆",
    "MO": "澳门",
    "XX": "未识别",
}

# Two-letter codes that are not regions: data units, protocol names, and line types.
ISO_CODE_BLOCKLIST: frozenset[str] = frozenset(
    {
        "gb", "mb", "tb", "kb", "pb",  # data units
        "ip", "ss", "vm", "ws", "tls", "tcp", "udp",
        "grpc", "http", "https", "cdn", "bgp", "iplc", "iepl", "nat",
        "doh", "dot", "dns", "cpu", "ram", "ssr", "vmess", "vless",
    }
)


def flag_of(code: str) -> str:
    """Convert a two-letter code to a flag emoji."""
    return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in code.upper())


# Flag emoji to region code, except XX: it is not a country and has no flag.
FLAGS: dict[str, str] = {flag_of(code): code for code in REGION_NAMES if code != "XX"}

# Sort the Chinese names by length, longest first, so that "新加坡" wins over a
# shorter prefix.
_CHINESE_SORTED: list[tuple[str, str]] = sorted(CHINESE_NAMES.items(), key=lambda kv: -len(kv[0]))

# Two-letter code: no letter or digit may touch either side. This rule stops
# the "PL" inside "IEPL" from matching Poland.
_ISO_RE = re.compile(r"(?<![A-Za-z0-9])([A-Za-z]{2})(?![A-Za-z0-9])")


def match_chinese_name(name: str) -> tuple[str, str] | None:
    """Return (matched word, region code). The longest match wins."""
    best: tuple[str, str] | None = None
    for word, code in _CHINESE_SORTED:
        if word in name and (best is None or len(word) > len(best[0])):
            best = (word, code)
    return best


def match_flag(name: str) -> tuple[str, str] | None:
    """Return (matched flag, region code). The earliest match wins."""
    best: tuple[int, str, str] | None = None
    for flag, code in FLAGS.items():
        pos = name.find(flag)
        if pos >= 0 and (best is None or pos < best[0]):
            best = (pos, flag, code)
    if best is None:
        return None
    return best[1], best[2]


def match_iso_code(name: str) -> tuple[str, str] | None:
    """Return (matched code, region code). The earliest match wins."""
    for m in _ISO_RE.finditer(name):
        code = m.group(1)
        if code.lower() in ISO_CODE_BLOCKLIST:
            continue
        upper = code.upper()
        if upper not in REGION_NAMES:
            continue
        return code, upper
    return None
