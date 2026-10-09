import time

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

KID = "test-kid"

def make_keypair():
    """
    Make a private key.
    """
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)

def make_jwks(private_key, kid=KID):
    """
    Make a JWKS from a private key.
    """
    jwk = jwt.algorithms.RSAAlgorithm.to_jwk(private_key.public_key(), as_dict=True)
    jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return {"keys": [jwk]}

def make_token(private_key, kid=KID, **overrides):
    """
    Make a token from a private key.
    """
    now = int(time.time())
    claims = {"sub": "0b9d3c1e-2f6a-4c55-9a7b-1d2e3f405162", "type": "access",
              "iss": "coika-auth", "aud": "coika-game", "iat": now, "exp": now + 1800}
    claims.update(overrides)
    claims = {k: v for k, v in claims.items() if v is not None}
    pem = private_key.private_bytes(serialization.Encoding.PEM,
                                    serialization.PrivateFormat.PKCS8,
                                    serialization.NoEncryption())
    return jwt.encode(claims, pem, algorithm="RS256", headers={"kid": kid})