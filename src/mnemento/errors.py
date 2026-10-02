"""Exceptions raised by Mnemento. Every rejected write raises one of these and stores nothing."""


class MnementoError(Exception):
    """Base class for all Mnemento errors."""


class InvalidTimeError(MnementoError, ValueError):
    """A time value is malformed or lacks a UTC offset (ADR-0004)."""


class SchemaDefinitionError(MnementoError, ValueError):
    """A schema definition is malformed."""


class SchemaNotFoundError(MnementoError, LookupError):
    """No schema is registered under the requested name/version."""


class BreakingSchemaChangeError(MnementoError):
    """A schema version bump contains destructive changes (ADR-0005).

    Destructive changes need a migration script that emits `migrated` events; that tooling
    is not part of v0, so the registry refuses them.
    """

    def __init__(self, name: str, reasons: list[str]):
        self.name = name
        self.reasons = reasons
        super().__init__(f"breaking change to schema {name!r}: " + "; ".join(reasons))


class DocumentValidationError(MnementoError, ValueError):
    """A document does not satisfy its schema."""

    def __init__(self, entity_type: str, errors: list[str]):
        self.entity_type = entity_type
        self.errors = errors
        super().__init__(f"{entity_type} document is invalid: " + "; ".join(errors))


class InvalidEventError(MnementoError, ValueError):
    """An event is malformed (unknown kind, bad payload, bad target...)."""


class EntityNotFoundError(MnementoError, LookupError):
    """The event refers to an entity that does not exist."""


class EntityExistsError(MnementoError):
    """A `created` event was recorded for an entity that already exists."""


class EntityRetractedError(MnementoError):
    """The entity has been retracted; no further events may be recorded on it."""


class ConflictError(MnementoError):
    """The event contradicts the current state (e.g. status_changed `from` does not match)."""


class UnknownFieldError(MnementoError, KeyError):
    """A query refers to a field the schema does not define."""
