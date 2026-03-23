from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional

import oci


@dataclass
class OCISettings:
    bucket_name: Optional[str]
    namespace: Optional[str]
    region: str
    compartment_id: Optional[str]
    tenancy: Optional[str]
    user: Optional[str]
    fingerprint: Optional[str]
    key_file: Optional[str]
    config_file: Optional[str]
    config_profile: str
    use_resource_principal: bool
    local_fallback_dir: str


class ObjectStoreClient:
    def __init__(self, settings: OCISettings):
        self.settings = settings
        self.local_fallback_dir = Path(settings.local_fallback_dir)
        self.local_fallback_dir.mkdir(parents=True, exist_ok=True)
        self.mode = "local"
        self._client = None

        try:
            self._client = self._build_client()
            if self._client and self.settings.namespace and self.settings.bucket_name:
                self.mode = "oci"
        except Exception:
            self._client = None
            self.mode = "local"

    @classmethod
    def from_config(cls, config) -> "ObjectStoreClient":
        settings = OCISettings(
            bucket_name=config.get("OCI_BUCKET_NAME"),
            namespace=config.get("OCI_NAMESPACE"),
            region=config.get("OCI_REGION"),
            compartment_id=config.get("OCI_COMPARTMENT"),
            tenancy=config.get("OCI_TENANCY_OCID"),
            user=config.get("OCI_USER_OCID"),
            fingerprint=config.get("OCI_FINGERPRINT"),
            key_file=config.get("OCI_API_KEY_FILE"),
            config_file=config.get("OCI_CONFIG_FILE"),
            config_profile=config.get("OCI_CONFIG_PROFILE"),
            use_resource_principal=config.get("OCI_USE_RESOURCE_PRINCIPAL"),
            local_fallback_dir=config.get("OBJECT_STORE_LOCAL_DIR"),
        )
        return cls(settings)

    def _build_client(self):
        if self.settings.use_resource_principal:
            signer = oci.auth.signers.get_resource_principals_signer()
            return oci.object_storage.ObjectStorageClient(config={"region": self.settings.region}, signer=signer)

        if self.settings.config_file:
            config = oci.config.from_file(self.settings.config_file, self.settings.config_profile)
            return oci.object_storage.ObjectStorageClient(config)

        required = [
            self.settings.tenancy,
            self.settings.user,
            self.settings.fingerprint,
            self.settings.key_file,
            self.settings.region,
        ]
        if all(required):
            config = {
                "tenancy": self.settings.tenancy,
                "user": self.settings.user,
                "fingerprint": self.settings.fingerprint,
                "key_file": self.settings.key_file,
                "region": self.settings.region,
            }
            return oci.object_storage.ObjectStorageClient(config)

        return None

    def save_text(self, object_name: str, text: str, metadata: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        if self.mode == "oci" and self._client:
            self._client.put_object(
                namespace_name=self.settings.namespace,
                bucket_name=self.settings.bucket_name,
                object_name=object_name,
                put_object_body=text.encode("utf-8"),
                opc_meta=metadata or {},
            )
            return {
                "object_name": object_name,
                "mode": "oci",
                "uri": f"oci://{self.settings.namespace}/{self.settings.bucket_name}/{object_name}",
            }

        destination = self.local_fallback_dir / object_name
        destination.write_text(text, encoding="utf-8")
        return {
            "object_name": object_name,
            "mode": "local",
            "uri": f"file://{destination}",
        }
