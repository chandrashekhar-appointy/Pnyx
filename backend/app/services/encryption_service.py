import os
import secrets
import logging
from typing import Tuple, Dict, Any
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.backends import default_backend

logger = logging.getLogger(__name__)

class EncryptionService:
    """
    Handles Zero-Knowledge Hybrid Encryption (AES-GCM + ECC Wrapping).
    """

    @staticmethod
    def encrypt_document(data: bytes, public_key_spki: str) -> Tuple[bytes, bytes, bytes]:
        """
        Encrypt data with a random AES-256-GCM session key and wrap the key with an ECC Public Key.

        Args:
            data: Raw bytes to encrypt.
            public_key_spki: SubjectPublicKeyInfo (SPKI) formatted ECC Public Key.

        Returns:
            (encrypted_data, wrapped_aes_key, nonce)
        """
        try:
            # 1. Generate random AES-256 session key
            aes_key = AESGCM.generate_key(bit_length=256)
            aesgcm = AESGCM(aes_key)
            nonce = secrets.token_bytes(12) # GCM standard nonce size

            # 2. Encrypt the actual data
            encrypted_data = aesgcm.encrypt(nonce, data, None)

            # 3. Load user's Public Key (ECC P-256)
            # Web Crypto API spki format maps to DER loaded via load_der_public_key
            # But usually it's PEM or DER. If it's the raw spki bytes, we need to handle it.
            # Assuming the public_key_spki is provided as a hex string or base64 of the DER.
            import base64
            public_key_bytes = base64.b64decode(public_key_spki)
            public_key = serialization.load_der_public_key(public_key_bytes, backend=default_backend())

            if not isinstance(public_key, ec.EllipticCurvePublicKey):
                raise ValueError("Public key must be an Elliptic Curve (ECC) key.")

            # 4. Wrap (Encrypt) the AES session key using ECC
            # For ECC, we typically use ECIES or simple ECDH. 
            # Web Crypto 'wrapKey' for P-256 often uses ECDH to derive a KEK.
            # However, for a simple backend "push" encryption, we can use a simpler approach
            # if the browser supports it. 
            # To stay aligned with Web Crypto 'wrapKey' for P-256:
            # We will use the 'cryptography' library's ec.exchange and then an HKDF to derive a KEK.
            # But a simpler way for pure public-key encryption is to use RSA or a dedicated ECIES library.
            
            # Since the user specifically asked for Pub/Pri keys and mentioned browser decryption:
            # Most robust way is ECIES.
            # Let's use a simpler "Envelope" model:
            # Generate a transient ECC key, perform exchange with user's public key, 
            # derive KEK via HKDF, encrypt AES key with KEK.
            
            transient_priv_key = ec.generate_private_key(ec.SECP256R1(), default_backend())
            shared_key = transient_priv_key.exchange(ec.ECDH(), public_key)
            
            # Derive KEK from shared secret
            from cryptography.hazmat.primitives.kdf.hkdf import HKDF
            kek = HKDF(
                algorithm=hashes.SHA256(),
                length=32,
                salt=None,
                info=b"pnyx-key-wrapping",
                backend=default_backend()
            ).derive(shared_key)
            
            # Wrap the AES key using the KEK
            kek_aesgcm = AESGCM(kek)
            kek_nonce = secrets.token_bytes(12)
            wrapped_aes_key = kek_aesgcm.encrypt(kek_nonce, aes_key, None)
            
            # We must provide the transient public key so the client can recreate the shared secret
            transient_pub_bytes = transient_priv_key.public_key().public_bytes(
                encoding=serialization.Encoding.DER,
                format=serialization.PublicFormat.SubjectPublicKeyInfo
            )

            # The full "wrapped payload" contains: [transient_pub][kek_nonce][wrapped_aes_key]
            # This allows the client (holding the private key) to:
            # 1. shared = ClientPri.exchange(transient_pub)
            # 2. kek = HKDF(shared)
            # 3. aes_key = AESGCM(kek).decrypt(kek_nonce, wrapped_aes_key)
            
            wrapper_payload = {
                "ephemeralPublicKey": base64.b64encode(transient_pub_bytes).decode('utf-8'),
                "kekNonce": base64.b64encode(kek_nonce).decode('utf-8'),
                "wrappedKey": base64.b64encode(wrapped_aes_key).decode('utf-8'),
                "nonce": base64.b64encode(nonce).decode('utf-8')
            }

            return encrypted_data, wrapper_payload

        except Exception as e:
            logger.error(f"Encryption failed: {e}", exc_info=True)
            raise

    @staticmethod
    def wrap_metadata(metadata: Dict[str, Any], public_key_spki: str) -> Dict[str, Any]:
        """Convenience to encrypt JSON metadata."""
        import json
        raw_json = json.dumps(metadata).encode('utf-8')
        enc_data, wrapper = EncryptionService.encrypt_document(raw_json, public_key_spki)
        import base64
        return {
            "encrypted_data": base64.b64encode(enc_data).decode('utf-8'),
            "wrapper": wrapper
        }
