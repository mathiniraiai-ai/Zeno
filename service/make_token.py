#!/usr/bin/env python3
"""Make a personal key for one team member.

    python service/make_token.py "Asha"

Prints the key (give it to Asha once; she puts it in her .env) and the line to add to ZENO_API_TOKENS in Railway.
Only the hash goes to Railway, so nobody can read the key back from there. To remove someone, delete their entry.
"""
import hashlib
import re
import secrets
import sys

if len(sys.argv) != 2 or not re.fullmatch(r"[A-Za-z0-9 ._-]{1,40}", sys.argv[1]):
    sys.exit('Usage: python service/make_token.py "Name"   (letters, digits, space . _ - only)')
name = sys.argv[1].strip()
token = "zeno_" + secrets.token_urlsafe(32)
digest = hashlib.sha256(token.encode()).hexdigest()
print(f"Person : {name}")
print(f"Key    : {token}   <- give this to {name} once; it cannot be shown again")
print(f"Railway: add this entry to ZENO_API_TOKENS (comma-separated with the others):\n         {name}:{digest}")
print("Their .env gets:\n         SCRIPTCUT_CLOUD_URL=https://<your-service-domain>\n         SCRIPTCUT_CLOUD_TOKEN=" + token)
