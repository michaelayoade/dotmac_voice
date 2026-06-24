from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from app.models.domain_settings import SettingDomain, SettingValueType

# Placeholder returned in read responses for secret settings so the value is
# never exposed in clear text while still signalling that one is configured.
SECRET_MASK = "********"  # noqa: S105 - display placeholder, not a password


class DomainSettingBase(BaseModel):
    domain: SettingDomain
    key: str
    value_type: SettingValueType = SettingValueType.string
    value_text: str | None = None
    value_json: dict | list | bool | int | str | None = None
    is_secret: bool = False
    is_active: bool = True


class DomainSettingCreate(DomainSettingBase):
    pass


class DomainSettingUpdate(BaseModel):
    domain: SettingDomain | None = None
    key: str | None = None
    value_type: SettingValueType | None = None
    value_text: str | None = None
    value_json: dict | list | bool | int | str | None = None
    is_secret: bool | None = None
    is_active: bool | None = None


class DomainSettingRead(DomainSettingBase):
    id: UUID
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def _mask_secret_value(self) -> "DomainSettingRead":
        """Never expose secret values in read responses.

        Secret settings (jwt_secret, TOTP/session keys, ...) are masked: if a
        value is configured it is replaced with a placeholder, otherwise left
        empty. The real value is only ever readable internally via the service
        layer, not through the admin API.
        """
        if self.is_secret:
            has_value = self.value_text is not None or self.value_json is not None
            self.value_text = SECRET_MASK if has_value else None
            self.value_json = None
        return self
