"""Config models for Cardigan API."""

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel


class ConfigValueType(str, Enum):
    """Valid config value types."""

    string = "string"
    int = "int"
    float = "float"
    bool = "bool"
    json = "json"


class ConfigItem(BaseModel):
    """Complete config item record."""

    key: str
    value: str
    value_type: ConfigValueType = ConfigValueType.string
    description: Optional[str] = None
    updated_at: datetime

    class Config:
        from_attributes = True

    def get_typed_value(self) -> Any:
        """Return value converted to its declared type."""
        if self.value_type == ConfigValueType.int:
            return int(self.value)
        elif self.value_type == ConfigValueType.float:
            return float(self.value)
        elif self.value_type == ConfigValueType.bool:
            return self.value.lower() in ("true", "1", "yes")
        elif self.value_type == ConfigValueType.json:
            import json

            return json.loads(self.value)
        return self.value


class ConfigCreate(BaseModel):
    """Schema for creating a config item."""

    key: str
    value: str
    value_type: ConfigValueType = ConfigValueType.string
    description: Optional[str] = None


class ConfigUpdate(BaseModel):
    """Schema for updating a config item."""

    value: Optional[str] = None
    value_type: Optional[ConfigValueType] = None
    description: Optional[str] = None
