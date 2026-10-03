"""JWT Authentication module — RS256 signature with access/refresh tokens."""

import logging
import os
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

# Try importing jwt, fall back gracefully
try:
    import jwt

    HAS_JWT = True
except ImportError:
    HAS_JWT = False
    logger.warning("PyJWT not installed, JWT auth disabled")


@dataclass
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int
    token_type: str = "Bearer"  # noqa: S105 -- protocol constant, not a credential


@dataclass
class JWTConfig:
    private_key_path: str = ""
    public_key_path: str = ""
    algorithm: str = "RS256"
    access_expire_minutes: int = 15
    refresh_expire_days: int = 7
    enabled: bool = False


def get_jwt_config() -> JWTConfig:
    """Load JWT config from environment variables."""
    return JWTConfig(
        private_key_path=os.environ.get("JWT_PRIVATE_KEY_PATH", ""),
        public_key_path=os.environ.get("JWT_PUBLIC_KEY_PATH", ""),
        algorithm=os.environ.get("JWT_ALGORITHM", "RS256"),
        access_expire_minutes=int(os.environ.get("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "15")),
        refresh_expire_days=int(os.environ.get("JWT_REFRESH_TOKEN_EXPIRE_DAYS", "7")),
        enabled=os.environ.get("JWT_ALGORITHM", "") != "",
    )


def _load_key(path: str) -> str:
    """Load PEM key from file."""
    if not path or not os.path.isfile(path):
        return ""
    with open(path) as f:
        return f.read()


def generate_keypair(output_dir: str = "./keys"):
    """Generate RS256 key pair for JWT signing. Call once during setup."""
    if not HAS_JWT:
        raise RuntimeError("PyJWT not installed")

    os.makedirs(output_dir, exist_ok=True)
    from cryptography.hazmat.backends import default_backend
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
        backend=default_backend(),
    )
    private_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_pem = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )

    private_path = os.path.join(output_dir, "private.pem")
    public_path = os.path.join(output_dir, "public.pem")
    with open(private_path, "wb") as f:
        f.write(private_pem)
    with open(public_path, "wb") as f:
        f.write(public_pem)
    os.chmod(private_path, 0o600)

    logger.info(f"Key pair generated: {private_path}, {public_path}")
    return private_path, public_path


#: Claims owned by the access-token issuer. `extra_claims` exists to carry genuine
#: extension claims, not to restate what the issuer already decided, so a collision on
#: any of these is a caller/config error and is rejected rather than silently applied.
#: Scoped to what this issuer actually sets — claims it does not manage (tenant_id,
#: session_id, ...) remain freely settable.
_ACCESS_TOKEN_RESERVED_CLAIMS = frozenset({"sub", "role_mask", "dept_mask", "iat", "exp", "type"})


def create_access_token(user_id: str, role_mask: int, dept_mask: int, extra_claims: dict = None) -> str:
    """Create a short-lived access token."""
    if not HAS_JWT:
        raise RuntimeError("PyJWT not installed")
    config = get_jwt_config()
    private_key = _load_key(config.private_key_path)
    if not private_key:
        raise RuntimeError("JWT private key not configured")

    now = int(time.time())
    payload = {
        "sub": user_id,
        "role_mask": role_mask,
        "dept_mask": dept_mask,
        "iat": now,
        "exp": now + config.access_expire_minutes * 60,
        "type": "access",
    }
    if extra_claims:
        # Fail closed on a reserved-claim collision. Filtering the colliding keys out
        # would silently swallow the caller's mistake; re-applying the canonical values
        # afterwards would let the caller believe it had set them. Neither is safe, so
        # the conflict is reported instead. Message lists only the colliding claim
        # names — never the token, key or full payload — and is sorted for determinism.
        collisions = _ACCESS_TOKEN_RESERVED_CLAIMS.intersection(extra_claims)
        if collisions:
            raise ValueError(
                "extra_claims cannot override reserved access-token claims: " + ", ".join(sorted(collisions))
            )
        payload.update(extra_claims)

    return jwt.encode(payload, private_key, algorithm=config.algorithm)


def create_refresh_token(user_id: str) -> str:
    """Create a long-lived refresh token."""
    if not HAS_JWT:
        raise RuntimeError("PyJWT not installed")
    config = get_jwt_config()
    private_key = _load_key(config.private_key_path)
    if not private_key:
        raise RuntimeError("JWT private key not configured")

    now = int(time.time())
    payload = {
        "sub": user_id,
        "iat": now,
        "exp": now + config.refresh_expire_days * 86400,
        "type": "refresh",
    }
    return jwt.encode(payload, private_key, algorithm=config.algorithm)


def create_token_pair(user_id: str, role_mask: int, dept_mask: int, extra_claims: dict = None) -> TokenPair:
    """Create both access and refresh tokens."""
    config = get_jwt_config()
    return TokenPair(
        access_token=create_access_token(user_id, role_mask, dept_mask, extra_claims),
        refresh_token=create_refresh_token(user_id),
        expires_in=config.access_expire_minutes * 60,
    )


def verify_token(token: str, token_type: str = "access") -> dict | None:  # noqa: S107 -- protocol default, not a credential
    """Verify and decode a JWT token. Returns payload or None."""
    if not HAS_JWT:
        return None
    config = get_jwt_config()
    public_key = _load_key(config.public_key_path)
    if not public_key:
        logger.warning("JWT public key not configured, skipping verification")
        return None
    try:
        payload = jwt.decode(token, public_key, algorithms=[config.algorithm])
        if payload.get("type") != token_type:
            return None
        return payload
    except jwt.ExpiredSignatureError:
        logger.debug("Token expired")
        return None
    except jwt.InvalidTokenError as e:
        logger.warning(f"Invalid token: {e}")
        return None


def extract_token_from_header(auth_header: str) -> str | None:
    """Extract Bearer token from Authorization header."""
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    return auth_header[7:].strip()
