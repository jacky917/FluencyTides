#!/usr/bin/env python3
"""輪詢正式站的版本端點，直到回報的 git_sha 等於本次 commit 才算部署完成。

Poll a production version endpoint until it reports the expected git_sha.

Portainer 的 stack webhook 是非同步的：收到請求就回 204，容器約數秒後才重建完成，
重建失敗也不會反映在 webhook 回應上。deploy job 因此在呼叫 webhook 後執行本腳本，
確認新映像真的在跑。

The Portainer stack webhook is asynchronous (204 on receipt, rebuild happens
afterwards, failures are not reported back), so the deploy job runs this after
calling it to confirm the new image is really serving.

用法 Usage:
    wait_for_deploy.py NAME URL EXPECTED_SHA [--timeout 300] [--interval 5]

- 端點須回傳 JSON 且含 ``git_sha`` 欄位（後端 /api/health、前端 /version.json）。
- 環境變數 CF_ACCESS_CLIENT_ID / CF_ACCESS_CLIENT_SECRET 有值時帶上
  Cloudflare Access service token header。
- 不跟隨轉址：Access 拒絕時回 302 轉登入頁，跟過去會拿到 200 的 HTML。
- 成功 exit 0；逾時 exit 1，並以 ``::error::`` 印出最後看到的狀態。
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """不跟隨轉址，讓 302 以 HTTPError 浮現。Surface redirects as errors."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _headers() -> dict[str, str]:
    # 明確的 User-Agent：預設的 Python-urllib 可能被 Cloudflare 的機器人防護擋下
    headers = {
        "Accept": "application/json",
        "Cache-Control": "no-cache",
        "User-Agent": "FluencyTides-deploy-check/1.0 (GitHub Actions)",
    }
    client_id = os.environ.get("CF_ACCESS_CLIENT_ID")
    client_secret = os.environ.get("CF_ACCESS_CLIENT_SECRET")
    if client_id and client_secret:
        headers["CF-Access-Client-Id"] = client_id
        headers["CF-Access-Client-Secret"] = client_secret
    return headers


def probe(url: str, timeout: float) -> tuple[str | None, str]:
    """請求一次，回傳 (git_sha 或 None, 給人看的狀態描述)。

    Fetch once; return (git_sha or None, human-readable observation).
    """
    # 加時間戳避免任何一層快取回舊值
    sep = "&" if "?" in url else "?"
    request = urllib.request.Request(f"{url}{sep}_={int(time.time())}", headers=_headers())
    try:
        with _opener.open(request, timeout=timeout) as response:
            body = response.read(4096).decode("utf-8", "replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        location = exc.headers.get("Location", "")
        hint = f" → {location[:120]}" if location else ""
        return None, f"HTTP {exc.code}{hint}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        return None, f"連線失敗: {reason}"

    try:
        sha = json.loads(body).get("git_sha")
    except (ValueError, AttributeError):
        return None, f"HTTP {status}，回應不是預期的 JSON: {body[:200]!r}"
    if not isinstance(sha, str):
        return None, f"HTTP {status}，回應缺少 git_sha: {body[:200]!r}"
    return sha, f"HTTP {status}，git_sha={sha}"


def wait(name: str, url: str, expected: str, timeout: float, interval: float) -> bool:
    print(f"⏳ 等待 {name} 上線 commit {expected}（{url}，最多 {timeout:g} 秒）")
    deadline = time.monotonic() + timeout
    last = None
    attempt = 0
    while True:
        attempt += 1
        sha, observed = probe(url, timeout=min(15.0, max(timeout, 1.0)))
        if observed != last:
            print(f"  [{attempt}] {observed}")
            last = observed
        if sha == expected:
            print(f"✅ {name} 已上線 {expected}")
            return True
        if time.monotonic() + interval > deadline:
            break
        time.sleep(interval)
    print(f"::error::{name} 在 {timeout:g} 秒內未上線 commit {expected}；最後一次（第 {attempt} 次）看到：{last}")
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("name", help="顯示用名稱，例如 backend")
    parser.add_argument("url", help="回傳含 git_sha 之 JSON 的端點")
    parser.add_argument("expected", help="預期的 commit sha")
    parser.add_argument("--timeout", type=float, default=300, help="秒，預設 300")
    parser.add_argument("--interval", type=float, default=5, help="秒，預設 5")
    args = parser.parse_args(argv)
    if not args.expected:
        print("::error::預期的 commit sha 為空")
        return 1
    ok = wait(args.name, args.url, args.expected, args.timeout, args.interval)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
