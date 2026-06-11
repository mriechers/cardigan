"""Auth service layer — consumer key management and audit logging.

Public API re-exported from submodules for convenient import:

    from api.services.auth import (
        ConsumerKeyRecord,
        create_consumer_key,
        lookup_consumer_key,
        touch_last_used,
        write_audit_log,
    )
"""

from api.services.auth.audit_log import write_audit_log
from api.services.auth.consumer_keys import (
    ConsumerKeyRecord,
    create_consumer_key,
    lookup_consumer_key,
    touch_last_used,
)

__all__ = [
    "ConsumerKeyRecord",
    "create_consumer_key",
    "lookup_consumer_key",
    "touch_last_used",
    "write_audit_log",
]
