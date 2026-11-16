"""Expiring bearer links for gift media. Never store signed URLs in the DB."""
import hashlib
import hmac
import re
import time
from urllib.parse import urlencode

from . import config

_NAME = re.compile(r"[A-Za-z0-9_-]+\.(?:jpg|jpeg|png|webp|gif|ogg|mp3|oga|m4a|wav|webm)\Z")


def valid_name(name):
    return bool(_NAME.fullmatch(name))


def signature(name, gift_id, expires):
    payload = f"media-v1\n{name}\n{gift_id}\n{expires}".encode()
    return hmac.new(config.MEDIA_SIGNING_SECRET.encode(), payload, hashlib.sha256).hexdigest()


def signed_url(path, gift_id):
    if not path or not path.startswith("/media/"):
        return None
    name = path[len("/media/"):]
    if not valid_name(name):
        return None
    expires = int(time.time()) + config.MEDIA_URL_TTL_SECONDS
    return f"/media/{name}?" + urlencode({"gift": gift_id, "expires": expires,
                                         "sig": signature(name, gift_id, expires)})


def verify(name, gift_id, expires, sig):
    return (valid_name(name) and bool(gift_id) and expires > int(time.time())
            and hmac.compare_digest(signature(name, gift_id, expires), sig))
