import bcrypt


def hash_password(plain_password: str) -> str:
    # Truncate to 72 bytes to satisfy bcrypt's limit
    pwd_bytes = plain_password[:72].encode("utf-8")
    # Generate a salt and hash
    salt = bcrypt.gensalt()
    hashed = bcrypt.hashpw(pwd_bytes, salt)
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    pwd_bytes = plain_password[:72].encode("utf-8")
    hashed_bytes = hashed_password.encode("utf-8")
    return bcrypt.checkpw(pwd_bytes, hashed_bytes)
