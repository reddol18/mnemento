"""Schema registry: register, look up and version schemas (ADR-0001, ADR-0005)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..errors import BreakingSchemaChangeError, SchemaDefinitionError, SchemaNotFoundError
from ..storage.base import SchemaRecord, Storage
from ..timeutil import DEFAULT_TZ, format_instant, now
from .definition import SchemaDef


class SchemaRegistry:
    def __init__(self, storage: Storage, tz: str = DEFAULT_TZ):
        self._storage = storage
        self._tz = tz
        self._cache: dict[tuple[str, int], SchemaDef] = {}

    def register(self, definition: SchemaDef | dict[str, Any], *, allow_gap: bool = False) -> SchemaDef:
        """Register a new schema or a new version of an existing one.

        - The first registration starts the history at whatever version the definition has (a new
          database can begin from the current definition).
        - A new version must be exactly latest + 1 and contain only additive changes;
          destructive changes raise BreakingSchemaChangeError (migration tooling is v2).
        - Re-registering a definition identical to the latest version is a no-op.
        - allow_gap (schema files): the file's version may be more than latest + 1 — a database that skipped
          intermediate file versions takes the newest one directly (still additive only).
        """
        schema = definition if isinstance(definition, SchemaDef) else SchemaDef.from_dict(definition)
        with self._storage.transaction():
            latest = self.get(schema.name) if schema.name in self.names() else None
            if latest is not None:
                if schema.to_dict() == latest.to_dict():
                    return latest
                if schema.version != latest.version + 1 and not (allow_gap and schema.version > latest.version):
                    raise SchemaDefinitionError(
                        f"{schema.name}: next version must be {latest.version + 1}, "
                        f"got {schema.version}"
                    )
                reasons = latest.breaking_changes_to(schema)
                if reasons:
                    raise BreakingSchemaChangeError(schema.name, reasons)
            self._storage.insert_schema(
                SchemaRecord(
                    name=schema.name,
                    version=schema.version,
                    definition=schema.to_dict(),
                    json_schema=schema.json_schema(),
                    created_at=format_instant(now(self._tz)),
                )
            )
            for fname in schema.indexed_fields:
                self._storage.ensure_indexed_field(fname)
        self._cache[(schema.name, schema.version)] = schema
        return schema

    def bump(self, definition: SchemaDef | dict[str, Any]) -> SchemaDef:
        """Register `definition` as the next version of its schema, whatever version it says."""
        schema = definition if isinstance(definition, SchemaDef) else SchemaDef.from_dict(
            {**definition, "version": definition.get("version", 1)}
        )
        latest = self.get(schema.name)
        return self.register(schema.with_version(latest.version + 1))

    def get(self, name: str, version: int | None = None) -> SchemaDef:
        if version is not None and (name, version) in self._cache:
            return self._cache[(name, version)]
        record = self._storage.get_schema(name, version)
        if record is None:
            what = f"{name} v{version}" if version else name
            raise SchemaNotFoundError(f"schema not registered: {what}")
        schema = SchemaDef.from_dict(record.definition)
        self._cache[(schema.name, schema.version)] = schema
        return schema

    def history(self, name: str) -> list[SchemaRecord]:
        return self._storage.list_schema_versions(name)

    def names(self) -> list[str]:
        return self._storage.list_schema_names()

    def load_dir(self, directory: str | Path) -> list[SchemaDef]:
        """Register every `*.json` schema definition in `directory` (sorted by file name).

        The database keeps its own version when
        - the file is older than the database's latest version (the database moved on), or
        - the user organized that type (ADR-0014 approvals): from then on the database is the dictionary's
          source, and a differing file is not applied (ADR-0018). The skipped files are in `load_notes`.
        """
        out = []
        self.load_notes: list[str] = []
        self.skipped_files: dict[str, dict[str, Any]] = {}  # organized types whose file differs (ADR-0023)
        names = set(self.names())
        for path in sorted(Path(directory).glob("*.json")):
            d = json.loads(path.read_text(encoding="utf-8"))
            if d.get("name") in names:
                latest = self.get(d["name"])
                fv = int(d.get("version", 1))
                organized = bool(self._storage.schema_changes(d["name"]))
                if fv < latest.version or (organized and SchemaDef.from_dict(d).to_dict() != latest.to_dict()):
                    if fv >= latest.version or organized:
                        self.load_notes.append(
                            f"{d['name']}: {path.name} v{fv} not applied; the database keeps v{latest.version}"
                            + (" (organized by the user; its improvements are offered by propose_schema)"
                               if organized else ""))
                    if organized:
                        self.skipped_files[d["name"]] = d
                    out.append(latest)
                    continue
            out.append(self.register(d, allow_gap=True))
        return out
