# app/tokens.py
import os

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

_serializer = URLSafeTimedSerializer(
    os.environ["SESSION_SECRET_KEY"], salt="bill-snapshot"
)

SNAPSHOT_TOKEN_MAX_AGE_SECONDS = 60 * 60 * 24 * 30  # 30 days


class TokenExpired(Exception):
    """The token's signature was valid, but it's older than the allowed window."""


class TokenInvalid(Exception):
    """The token's signature doesn't check out at all — malformed or tampered."""


def make_snapshot_token(account_number: str) -> str:
    return _serializer.dumps(account_number)


def read_snapshot_token(
    token: str, max_age: int = SNAPSHOT_TOKEN_MAX_AGE_SECONDS
) -> str:
    try:
        return _serializer.loads(token, max_age=max_age)
    except SignatureExpired:
        raise TokenExpired from None
    except BadSignature:
        raise TokenInvalid from None
