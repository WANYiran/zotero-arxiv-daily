"""Publisher client used by the existing daily pipeline (no WeChat credentials)."""

from datetime import datetime
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests


class Publisher:
    def __init__(self, url: str, token: str):
        parsed = urlparse(url)
        if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost")):
            raise ValueError("Assistant URL must use HTTPS (localhost HTTP is allowed for testing)")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Assistant URL must not include credentials, query, or fragment")
        if len(token) < 32:
            raise ValueError("Missing or invalid assistant token")
        self.url, self.token = url.rstrip("/"), token

    def _request(self, method, path, **kwargs):
        try:
            response = requests.request(method, self.url + path, timeout=30, allow_redirects=False,
                                        headers={"Authorization": "Bearer " + self.token}, **kwargs)
            if not 200 <= response.status_code < 300:
                raise RuntimeError("Assistant request failed")
            return response.json()
        except (requests.RequestException, ValueError):
            raise RuntimeError("Assistant service unavailable; check server and configuration") from None

    def preferences(self):
        prefs = self._request("GET", "/preferences")
        for key in ("interests", "avoid"):
            if not isinstance(prefs.get(key), list) or any(not isinstance(x, str) for x in prefs[key]):
                raise ValueError("Invalid assistant preferences")
        return prefs

    def publish(self, papers, deliver=True):
        payload = []
        for p in papers:
            payload.append({"title": p.title[:1000], "url": p.url, "pdf_url": p.pdf_url,
                            "abstract": p.abstract[:12000], "full_text": (p.full_text or "")[:16000] or None,
                            "tldr": (p.tldr or "")[:4000] or None,
                            "score": float(p.score) if p.score is not None else None})
        return self._request("POST", "/digests", json={
            "date": str(datetime.now(ZoneInfo("Asia/Shanghai")).date()),
            "papers": payload, "deliver": deliver,
        })
