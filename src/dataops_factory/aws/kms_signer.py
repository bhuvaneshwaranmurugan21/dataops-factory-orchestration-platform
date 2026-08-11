from __future__ import annotations

from typing import Any, Protocol


class KmsClient(Protocol):
    def sign(self, **kwargs: Any) -> dict[str, Any]: ...

    def verify(self, **kwargs: Any) -> dict[str, Any]: ...


class KmsDigestSigner:
    """Asymmetric KMS implementation of the manifest signer contract."""

    def __init__(
        self,
        client: KmsClient,
        key_arn: str,
        algorithm: str = "ECDSA_SHA_256",
    ) -> None:
        self._client = client
        self._key_arn = key_arn
        self._algorithm = algorithm

    @property
    def key_id(self) -> str:
        return self._key_arn

    @property
    def algorithm(self) -> str:
        return self._algorithm

    def sign_digest(self, digest: bytes) -> bytes:
        response = self._client.sign(
            KeyId=self._key_arn,
            Message=digest,
            MessageType="DIGEST",
            SigningAlgorithm=self._algorithm,
        )
        return bytes(response["Signature"])

    def verify_digest(self, digest: bytes, signature: bytes) -> bool:
        response = self._client.verify(
            KeyId=self._key_arn,
            Message=digest,
            MessageType="DIGEST",
            Signature=signature,
            SigningAlgorithm=self._algorithm,
        )
        return bool(response["SignatureValid"])
