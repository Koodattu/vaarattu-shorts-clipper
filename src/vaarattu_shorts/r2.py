"""Upload final revisions to the dedicated public publishing bucket."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import quote, urlencode
from xml.etree import ElementTree

import httpx

from .delivery import checked_path
from .config import load_env, save_env
from .processes import lock
from .storage import atomic_json, digest

ACCOUNT = "ea876d47d27f4c235e178a375c1ed651"
BUCKET = "vaarattu-shorts-publishing"
PUBLIC_URL = "https://publishing.vaarattu.tv"
STORAGE_LIMIT = 8_000_000_000


def credentials(settings):
    load_env(settings.root / ".env")
    access_key, secret_key = os.environ.get("R2_ACCESS_KEY_ID"), os.environ.get("R2_SECRET_ACCESS_KEY")
    if not access_key or not secret_key:
        raise ValueError("Connect Cloudflare storage in Publishing first.")
    return {"access_key": access_key, "secret_key": secret_key}


class Client:
    def __init__(self, access_key, secret_key):
        self.access_key, self.secret_key = access_key, secret_key
        self.host = f"{ACCOUNT}.r2.cloudflarestorage.com"

    def request(self, method, key="", *, query=None, content=b"", sha256=None, size=None):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        scope = f"{stamp[:8]}/auto/s3/aws4_request"
        path = "/" + BUCKET + ("/" + quote(key, safe="/-_.~") if key else "")
        query_string = urlencode(sorted((query or {}).items()), quote_via=quote, safe="~")
        payload = sha256 or hashlib.sha256(b"").hexdigest()
        headers = {"host": self.host, "x-amz-content-sha256": payload, "x-amz-date": stamp}
        if method == "PUT":
            headers["content-type"] = "video/mp4"
            headers["content-length"] = str(size)
        signed = ";".join(sorted(headers))
        canonical = "\n".join([method, path, query_string,
                                "".join(f"{k}:{headers[k]}\n" for k in sorted(headers)), signed, payload])
        signing_key = ("AWS4" + self.secret_key).encode()
        for part in (stamp[:8], "auto", "s3", "aws4_request"):
            signing_key = hmac.new(signing_key, part.encode(), hashlib.sha256).digest()
        signature = hmac.new(signing_key,
                             f"AWS4-HMAC-SHA256\n{stamp}\n{scope}\n{hashlib.sha256(canonical.encode()).hexdigest()}".encode(),
                             hashlib.sha256).hexdigest()
        headers["authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self.access_key}/{scope}, SignedHeaders={signed}, Signature={signature}"
        )
        try:
            with httpx.Client(timeout=httpx.Timeout(120, connect=20), follow_redirects=False, trust_env=False) as client:
                response = client.request(method, f"https://{self.host}{path}?{query_string}",
                                          headers=headers, content=content)
        except httpx.HTTPError:
            raise ValueError("Cloudflare could not be reached. Try again in a moment.") from None
        if not response.is_success:
            raise ValueError(f"Cloudflare could not complete the request (HTTP {response.status_code}). Check the bucket key.")
        return response

    def used_bytes(self):
        total, continuation = 0, ""
        for _ in range(100):
            query = {"list-type": "2", "max-keys": "1000"}
            if continuation:
                query["continuation-token"] = continuation
            try:
                root = ElementTree.fromstring(self.request("GET", query=query).content)
                if root.tag.rsplit("}", 1)[-1] != "ListBucketResult":
                    raise ValueError("Unexpected storage listing")
                total += sum(int(e.text) for e in root.findall("{*}Contents/{*}Size"))
                if root.findtext("{*}IsTruncated") == "false":
                    return total
                next_token = root.findtext("{*}NextContinuationToken")
                if not next_token or next_token == continuation:
                    break
                continuation = next_token
            except (ElementTree.ParseError, TypeError, ValueError):
                raise ValueError("Cloudflare storage usage could not be checked. No video was uploaded.") from None
        raise ValueError("The storage listing is incomplete. No video was uploaded.")


def connect(settings, access_key, secret_key):
    if not isinstance(access_key, str) or not isinstance(secret_key, str):
        raise ValueError("Enter the Cloudflare access key ID and secret access key.")
    access_key, secret_key = access_key.strip(), secret_key.strip()
    if not re.fullmatch(r"[a-fA-F0-9]{32}", access_key) or not re.fullmatch(r"[a-fA-F0-9]{64}", secret_key):
        raise ValueError("Use the S3 access key ID and secret access key shown by Cloudflare.")
    used = Client(access_key, secret_key).used_bytes()
    values = {"R2_ACCESS_KEY_ID": access_key, "R2_SECRET_ACCESS_KEY": secret_key}
    save_env(settings.root / ".env", values)
    os.environ.update(values)
    return {"connected": True, "used_bytes": used}


def uploads(settings):
    path = settings.work / "r2-uploads.json"
    return json.loads(path.read_text()) if path.exists() else {}


def status(settings):
    load_env(settings.root / ".env")
    return {"connected": bool(os.environ.get("R2_ACCESS_KEY_ID") and os.environ.get("R2_SECRET_ACCESS_KEY")), "bucket": BUCKET,
            "public_url": PUBLIC_URL, "storage_limit": STORAGE_LIMIT, "uploads": uploads(settings)}


def verify_public(record):
    try:
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
            response = client.head(record["url"])
        if (not response.is_success or int(response.headers.get("content-length", "-1")) != record["bytes"]
                or response.headers.get("content-type", "").split(";")[0] != "video/mp4"):
            raise ValueError("Unexpected public video response")
    except (httpx.HTTPError, ValueError):
        raise ValueError("The upload is saved, but its public video link is not available yet. Check the domain and retry.") from None


def upload(settings, store, clip_id, revision):
    with lock(settings.work / "r2-upload.lock"):
        clip = store.clip(clip_id)
        if clip["revision"] != revision or clip["review_status"] != "ready_to_post":
            raise ValueError("Mark this exact clip revision Ready for posting before uploading it.")
        path = checked_path(settings.ready / clip_id / str(revision) / "short.mp4", settings.work)
        if not path.is_file() or not 0 < path.stat().st_size <= 2_000_000_000:
            raise ValueError("The final video is missing, empty, or larger than 2 GB.")
        fingerprint = digest(path)
        if fingerprint != clip["body"].get("video_sha256"):
            raise ValueError("The final video changed. Render and review it again before uploading.")
        saved = uploads(settings)
        identity = f"{clip_id}:{revision}:{fingerprint}"
        if identity in saved:
            verify_public(saved[identity])
            return saved[identity]
        client = Client(**credentials(settings))
        if client.used_bytes() + path.stat().st_size > STORAGE_LIMIT:
            raise ValueError("Uploading would exceed this app's 8 GB storage limit. Remove published cloud copies first.")
        key = f"clips/{fingerprint}.mp4"
        with path.open("rb") as media:
            client.request("PUT", key, content=media, sha256=fingerprint, size=path.stat().st_size)
        current = store.clip(clip_id)
        record = {"clip_id": clip_id, "revision": revision, "sha256": fingerprint,
                  "url": f"{PUBLIC_URL}/{key}", "bytes": path.stat().st_size,
                  "uploaded_at": datetime.now(timezone.utc).isoformat()}
        saved[identity] = record
        atomic_json(settings.work / "r2-uploads.json", saved)
        verify_public(record)
        if current["revision"] != revision or current["review_status"] != "ready_to_post":
            raise ValueError("The clip changed during upload. Review the current revision before publishing.")
        return record
