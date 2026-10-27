"""Ed25519 keys: the studio seals packages, the site seals result bundles."""
import base64
import hashlib
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey


def key_id(public: Ed25519PublicKey) -> str:
    raw = public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return hashlib.sha256(raw).hexdigest()[:16]


def generate(prefix: Path) -> str:
    """Write <prefix>.key (keep secret) and <prefix>.pub (copy to the other side's trust dir)."""
    private = Ed25519PrivateKey.generate()
    prefix.parent.mkdir(parents=True, exist_ok=True)
    key_path, pub_path = Path(f"{prefix}.key"), Path(f"{prefix}.pub")
    key_path.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                               serialization.NoEncryption()))
    try:
        key_path.chmod(0o600)
    except OSError:
        pass
    pub_path.write_bytes(private.public_key().public_bytes(serialization.Encoding.PEM,
                                                           serialization.PublicFormat.SubjectPublicKeyInfo))
    return key_id(private.public_key())


def load_private(path: Path) -> Ed25519PrivateKey:
    key = serialization.load_pem_private_key(path.read_bytes(), password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise ValueError(f"{path} 不是 Ed25519 私钥")
    return key


def load_public(path: Path) -> Ed25519PublicKey:
    key = serialization.load_pem_public_key(path.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError(f"{path} 不是 Ed25519 公钥")
    return key


def load_trust_dir(directory: Path) -> dict[str, Ed25519PublicKey]:
    trusted = {}
    for path in sorted(directory.glob("*.pub")):
        public = load_public(path)
        trusted[key_id(public)] = public
    return trusted


def sign(private: Ed25519PrivateKey, data: bytes) -> dict:
    return {"algorithm": "ed25519", "key_id": key_id(private.public_key()),
            "signature": base64.b64encode(private.sign(data)).decode()}


def verify(trusted: dict[str, Ed25519PublicKey], envelope: dict, data: bytes) -> str:
    """Return the signer's key id or raise ValueError."""
    if envelope.get("algorithm") != "ed25519":
        raise ValueError("只接受 ed25519 签名")
    public = trusted.get(str(envelope.get("key_id", "")))
    if public is None:
        raise ValueError(f"签名密钥 {envelope.get('key_id')} 不在信任列表中")
    try:
        public.verify(base64.b64decode(envelope["signature"], validate=True), data)
    except (InvalidSignature, ValueError, KeyError, TypeError) as exc:
        raise ValueError("签名校验失败：内容被修改或签名无效") from exc
    return envelope["key_id"]
