"""Validate TLS and bearer auth without logging credentials or private preferences."""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def main():
    url = os.environ.get("ASSISTANT_URL", "").rstrip("/")
    token = os.environ.get("ASSISTANT_TOKEN", "")
    if not url:
        print("SKIP: ASSISTANT_URL is not configured.")
        return 0
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or not token:
        print("FAIL: Configure a clean HTTPS ASSISTANT_URL and ASSISTANT_TOKEN.")
        return 1
    opener = urllib.request.build_opener(NoRedirect())
    try:
        with opener.open(url + "/health", timeout=20) as response:
            if json.load(response).get("status") != "ok":
                raise ValueError("health")
        try:
            opener.open(url + "/preferences", timeout=20)
        except urllib.error.HTTPError as exc:
            if exc.code != 401:
                raise
        else:
            raise ValueError("auth")
        request = urllib.request.Request(url + "/preferences", headers={"Authorization": "Bearer " + token})
        with opener.open(request, timeout=20) as response:
            if not isinstance(json.load(response), dict):
                raise ValueError("preferences")
    except Exception:
        print("FAIL: HTTPS health or authentication check failed; response content was not logged.")
        return 1
    print("PASS: trusted HTTPS; service healthy; anonymous access rejected; configured token accepted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
