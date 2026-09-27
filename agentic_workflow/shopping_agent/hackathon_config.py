"""Reversible hackathon credential obfuscation, not secure secret storage.
Anyone with this file can recover the key. Revoke it after judging.
"""

import base64

_PAYLOAD = 'c9gSk92AO7hj6o/5hJFOsSw8EUdQFV4Z8/9HxkB82qWxNCM='
_MASK = 'ALM/9ey2CY9V2u7P4Kd7hRtfdSY2IWktwc9x8XVMv8aEARY='


def get_deepseek_api_key() -> str:
    payload = base64.b64decode(_PAYLOAD)
    mask = base64.b64decode(_MASK)
    return bytes(a ^ b for a, b in zip(payload, mask)).decode('utf-8')
