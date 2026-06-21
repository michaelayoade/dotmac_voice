from pydantic import BaseModel, ConfigDict, Field


class ExtensionIntent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    number: str = Field(min_length=1, max_length=32)
    display_name: str = ""


class DomainIntent(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    fusionpbx_domain: str = Field(min_length=1, max_length=255)
    extensions: list[ExtensionIntent] = []


class DomainSyncResult(BaseModel):
    customer_id: str
    sync_status: str
