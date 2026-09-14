from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import requests

from ..config import settings


def collect_reference_material(job: dict[str, Any], defects: list[dict[str, Any]]) -> dict[str, Any]:
    local_sources = collect_local_knowledge()
    if not settings.web_search_enabled:
        return {"enabled": False, "queries": [], "sources": local_sources, "note": "联网检索未启用，已使用本地知识库资料。" if local_sources else "联网检索未启用。"}

    classes = sorted({str(item.get("class_name") or "缺陷") for item in defects})[:5]
    object_name = str(job.get("inspected_object") or job.get("project_name") or "工业图像缺陷")
    subject = object_name if settings.web_search_send_project_context else "工业检测对象"
    queries = [
        f"{subject} 缺陷 检测 复核 建议",
        "焊缝 缺陷 图像 检测 裂纹 气孔 夹渣 复核",
        "无损检测 缺陷 报告 物理坐标 标定",
    ]
    for class_name in classes:
        queries.append(f"{subject} {class_name} 缺陷 风险 复检")

    sources: list[dict[str, str]] = []
    for query in queries[:4]:
        for item in _search(query):
            if item["url"] not in {source["url"] for source in sources}:
                sources.append(item)
            if len(sources) >= settings.web_search_max_results:
                break
        if len(sources) >= settings.web_search_max_results:
            break

    return {
        "enabled": True,
        "provider": settings.web_search_provider,
        "queries": queries[:4],
        "sources": local_sources + sources,
        "note": "" if (sources or local_sources) else "未取得可用外部资料，可能是网络不可用、检索服务未配置，且本地 knowledge 目录为空。",
    }


def collect_local_knowledge(query: str = "") -> list[dict[str, str]]:
    knowledge_dir = settings.resource_dir / "knowledge"
    if not knowledge_dir.exists():
        return []
    sources: list[dict[str, str]] = []
    terms = {term.lower() for term in re.split(r"\s+", query) if len(term) >= 2}
    ranked: list[tuple[int, dict[str, str]]] = []
    for path in sorted(knowledge_dir.rglob("*")):
        if path.suffix.lower() not in {".md", ".txt", ".json"} or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8-sig", errors="ignore")
        except OSError:
            continue
        snippet = re.sub(r"\s+", " ", text).strip()[:900]
        if snippet:
            score = sum(1 for term in terms if term in text.lower())
            ranked.append((score, {"title": f"本地知识库：{path.name}", "url": str(path), "snippet": snippet}))
    ranked.sort(key=lambda item: (-item[0], item[1]["title"]))
    return [item[1] for item in ranked[:8]]


def _search(query: str) -> list[dict[str, str]]:
    provider = settings.web_search_provider.lower()
    if provider == "serper":
        return _search_serper(query)
    if provider == "tavily":
        return _search_tavily(query)
    if provider == "bing":
        return _search_bing(query)
    return _search_duckduckgo(query)


def _search_duckduckgo(query: str) -> list[dict[str, str]]:
    try:
        response = requests.get(
            f"https://duckduckgo.com/html/?q={quote_plus(query)}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=12,
        )
        response.raise_for_status()
    except Exception:
        return []
    results: list[dict[str, str]] = []
    pattern = re.compile(
        r'<a rel="nofollow" class="result__a" href="(?P<url>.*?)".*?>(?P<title>.*?)</a>.*?'
        r'<a class="result__snippet".*?>(?P<snippet>.*?)</a>',
        re.S,
    )
    for match in pattern.finditer(response.text):
        title = _clean_html(match.group("title"))
        snippet = _clean_html(match.group("snippet"))
        url = html.unescape(match.group("url"))
        if title and url:
            results.append({"title": title, "url": url, "snippet": snippet})
    return results


def _search_serper(query: str) -> list[dict[str, str]]:
    if not settings.web_search_api_key:
        return []
    try:
        response = requests.post(
            settings.web_search_api_url or "https://google.serper.dev/search",
            headers={"X-API-KEY": settings.web_search_api_key, "Content-Type": "application/json"},
            json={"q": query, "num": settings.web_search_max_results},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        return []
    return [
        {"title": item.get("title", ""), "url": item.get("link", ""), "snippet": item.get("snippet", "")}
        for item in data.get("organic", [])
        if item.get("link")
    ]


def _search_tavily(query: str) -> list[dict[str, str]]:
    if not settings.web_search_api_key:
        return []
    try:
        response = requests.post(
            settings.web_search_api_url or "https://api.tavily.com/search",
            json={"api_key": settings.web_search_api_key, "query": query, "max_results": settings.web_search_max_results},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        return []
    return [
        {"title": item.get("title", ""), "url": item.get("url", ""), "snippet": item.get("content", "")}
        for item in data.get("results", [])
        if item.get("url")
    ]


def _search_bing(query: str) -> list[dict[str, str]]:
    if not settings.web_search_api_key:
        return []
    try:
        response = requests.get(
            settings.web_search_api_url or "https://api.bing.microsoft.com/v7.0/search",
            headers={"Ocp-Apim-Subscription-Key": settings.web_search_api_key},
            params={"q": query, "count": settings.web_search_max_results},
            timeout=15,
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        return []
    return [
        {"title": item.get("name", ""), "url": item.get("url", ""), "snippet": item.get("snippet", "")}
        for item in data.get("webPages", {}).get("value", [])
        if item.get("url")
    ]


def _clean_html(value: str) -> str:
    text = re.sub(r"<.*?>", "", value)
    return html.unescape(re.sub(r"\s+", " ", text)).strip()
