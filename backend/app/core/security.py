import hashlib
import secrets

def generate_email_address(domain: str) -> str:
    """Generate a random email address with the given domain."""
    local_part = secrets.token_hex(8)  # Generate a random local part

    return f"{local_part}@{domain}"

def generate_access_token() -> str:
    """Generate a secure access token."""
    return secrets.token_urlsafe(32)

def hash_token(token: str) -> str:
    """Hash the token using SHA-256."""
    return hashlib.sha256(
        token.encode("utf-8") #This encodes the token to bytes before hashing
    ).hexdigest() # This returns the hexadecimal representation of the hash