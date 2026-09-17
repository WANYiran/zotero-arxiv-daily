"""Check repository secrets; never log keys, user identifiers, or library content."""

import json
import os
import sys
import urllib.error
import urllib.request


def verify(user_id, key, opener=urllib.request.urlopen):
    user_id, key = user_id.strip(), key.strip()
    if not user_id.isascii() or not user_id.isdigit() or not key:
        raise ValueError("ZOTERO_ID must be numeric and ZOTERO_KEY must be set.")

    def get(path):
        request = urllib.request.Request(
            "https://api.zotero.org" + path,
            headers={"Zotero-API-Key": key, "Zotero-API-Version": "3"},
        )
        with opener(request, timeout=30) as response:
            return json.load(response), response.headers

    identity, _ = get("/keys/current")
    if str(identity.get("userID")) != user_id:
        raise ValueError("ZOTERO_ID does not match the owner of ZOTERO_KEY.")
    access = identity.get("access", {}).get("user", {})
    if access.get("library") is not True:
        raise ValueError("Enable Allow library access for this Zotero key.")
    if access.get("write"):
        raise ValueError("Disable Allow write access to use a read-only Zotero key.")
    items, headers = get(f"/users/{user_id}/items/top?format=json&limit=100")
    if not isinstance(items, list):
        raise ValueError("Unexpected Zotero response format.")
    with_abstract = sum(bool(item.get("data", {}).get("abstractNote", "").strip()) for item in items)
    total = headers.get("Total-Results", "unknown")
    # Only a numeric count is allowed to reach logs, even from an unexpected server response.
    total = str(int(total)) if str(total).isascii() and str(total).isdigit() else "unknown"
    return f"PASS: user ID matches; library readable; write access disabled. Top-level items: {total}; sampled: {len(items)}; sampled items with abstracts: {with_abstract}."


def main():
    try:
        print(verify(os.environ.get("ZOTERO_ID", ""), os.environ.get("ZOTERO_KEY", "")))
        return 0
    except urllib.error.HTTPError as exc:
        print(f"FAIL: Zotero returned HTTP {exc.code}; verify key permissions and user ID.")
    except ValueError as exc:
        # Only locally generated validation messages; response parsing errors are generic.
        if isinstance(exc, json.JSONDecodeError):
            print("FAIL: Zotero returned invalid JSON.")
        else:
            print("FAIL: " + str(exc))
    except Exception:
        print("FAIL: Zotero request could not be completed; no response content was logged.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
