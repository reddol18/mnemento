"""Find Mnemento databases, and build schema-based readable views of them (ADR-0031).

    mnemento-db list [--scan DIR ...]          databases named by MCP configs (MNEMENTO_DB)
    mnemento-db open [NAME|NUMBER|PATH]        open the raw database in a viewer
    mnemento-db view [NAME|NUMBER|PATH]        build <db dir>/views/<name>-view.db and open it

The view is a snapshot made without any LLM: one flat table per record type (schema
fields as columns, enum codes as their first label, references as the target's name),
plus _summary, _fields, _history and _meta. The source database is opened read-only.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

NAME_KEYS = ("name", "title", "code")
REQUIRED_TABLES = {"entities", "events", "schemas"}


# --------------------------------------------------------------------------- views


def _qi(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _first_label(spec: dict | None, value: Any) -> Any:
    labels = (spec or {}).get("labels") or {}
    got = labels.get(value) if isinstance(value, str) else None
    if isinstance(got, list) and got:
        return got[0]
    if isinstance(got, str):
        return got
    return value


class _Formatter:
    def __init__(self, names: dict[str, str]):
        self.names = names

    def value(self, spec: dict | None, value: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, list):
            item_spec = dict((spec or {}).get("items") or {})
            if spec and spec.get("ref") and not item_spec.get("ref"):
                item_spec["ref"] = spec["ref"]
            if all(not isinstance(v, (dict, list)) for v in value):
                return ", ".join("" if p is None else str(p) for p in (self.value(item_spec, v) for v in value))
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, dict):
            return json.dumps(value, ensure_ascii=False)
        if isinstance(value, bool):
            return "yes" if value else "no"
        if spec and spec.get("ref") and isinstance(value, str):
            return self.names.get(value, value)
        return _first_label(spec, value)

    def changes(self, fields: dict, payload: dict) -> str:
        return ", ".join(f"{k}={self.value(fields.get(k), v)}" for k, v in payload.items())


def _latest_schemas(src: sqlite3.Connection) -> dict[str, dict]:
    rows = src.execute(
        "SELECT name, definition FROM schemas s "
        "WHERE version = (SELECT max(version) FROM schemas WHERE name = s.name)"
    )
    return {name: json.loads(definition) for name, definition in rows}


def _column_names(fields: dict) -> list[str]:
    """A reference field shows the target's name, so 'company_id' becomes 'company'."""
    taken = set(fields)
    out = []
    for key, spec in fields.items():
        name = key
        if spec.get("ref") and key.endswith("_id") and key[:-3] not in taken:
            name = key[:-3]
            taken.add(name)
        out.append(name)
    return out


def _field_row(etype: str, key: str, spec: dict, source: str) -> tuple:
    labels = spec.get("labels") or {}
    label_text = ", ".join(
        f"{code}={(v[0] if isinstance(v, list) and v else v)}" for code, v in labels.items()
    ) or (", ".join(map(str, spec["enum"])) if spec.get("enum") else None)
    return (
        etype, key, spec.get("type"), spec.get("ref"), label_text,
        "yes" if spec.get("required") else None, spec.get("description"), source,
    )


def build_view(source: str | Path, out: str | Path) -> Path:
    """Write a readable snapshot of ``source`` to ``out`` and return the path written.

    If ``out`` is locked (open in a viewer on Windows), a time-stamped sibling is written instead.
    """
    source, out = Path(source), Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    src = sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True)
    try:
        schemas = _latest_schemas(src)
        entities = src.execute(
            "SELECT id, type, doc, retracted, created_at, updated_at FROM entities ORDER BY rowid"
        ).fetchall()
        docs = {eid: json.loads(doc) for eid, _t, doc, *_ in entities}

        names: dict[str, str] = {}
        for eid, d in docs.items():
            for key in NAME_KEYS:
                if isinstance(d.get(key), str) and d[key].strip():
                    names[eid] = d[key]
                    break
        # A record without a name of its own (an application) takes its first reference's name.
        for eid, etype, *_ in entities:
            if eid in names:
                continue
            for key, spec in ((schemas.get(etype) or {}).get("fields") or {}).items():
                target = docs[eid].get(key)
                if spec.get("ref") and isinstance(target, str) and target in names:
                    names[eid] = names[target]
                    break
        fmt = _Formatter(names)

        tmp = out.with_name(out.name + ".tmp")
        tmp.unlink(missing_ok=True)
        dst = sqlite3.connect(tmp)
        field_rows: list[tuple] = []
        summary_rows: list[tuple] = []

        by_type: dict[str, list] = {}
        for row in entities:
            by_type.setdefault(row[1], []).append(row)
        for etype in sorted(by_type):
            rows = by_type[etype]
            schema = schemas.get(etype) or {}
            fields = schema.get("fields") or {}
            live = [r for r in rows if not r[3]]
            drafts: list[str] = []
            for r in live:
                for key in docs[r[0]]:
                    if key not in fields and key not in drafts and key != "schema_version":
                        drafts.append(key)
            # display_name, not name: a schema may have its own "name" field (company does).
            cols = ["id", "display_name"] + _column_names(fields) + drafts + ["created_at", "updated_at"]
            dst.execute(f"CREATE TABLE {_qi(etype)} ({', '.join(_qi(c) for c in cols)})")
            data = []
            for r in live:
                d = docs[r[0]]
                data.append(
                    [r[0], names.get(r[0])]
                    + [fmt.value(fields.get(k), d.get(k)) for k in fields]
                    + [fmt.value(None, d.get(k)) for k in drafts]
                    + [r[4], r[5]]
                )
            dst.executemany(f"INSERT INTO {_qi(etype)} VALUES ({', '.join('?' * len(cols))})", data)
            field_rows += [_field_row(etype, k, s, "schema") for k, s in fields.items()]
            field_rows += [(etype, k, None, None, None, None, None, "draft") for k in drafts]
            summary_rows.append(
                (etype, "entity", len(live), len(rows) - len(live),
                 max((r[5] or "" for r in rows), default=None), schema.get("description"))
            )

        has_series = src.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='series_points'"
        ).fetchone()
        for etype, schema in sorted(schemas.items()):
            if schema.get("kind") != "series" or not has_series or etype in by_type:
                continue
            fields = schema.get("fields") or {}
            points = src.execute(
                "SELECT t, doc FROM series_points WHERE type = ? ORDER BY key, t", (etype,)
            ).fetchall()
            cols = _column_names(fields) or ["doc"]
            dst.execute(f"CREATE TABLE {_qi(etype)} ({', '.join(_qi(c) for c in cols)})")
            data = []
            for _t, doc in points:
                d = json.loads(doc)
                data.append([fmt.value(fields.get(k), d.get(k)) for k in fields] if fields else [doc])
            dst.executemany(f"INSERT INTO {_qi(etype)} VALUES ({', '.join('?' * len(cols))})", data)
            field_rows += [_field_row(etype, k, s, "schema") for k, s in fields.items()]
            summary_rows.append(
                (etype, "series", len(points), 0, max((p[0] for p in points), default=None),
                 schema.get("description"))
            )

        history = []
        for seq, eid, etype, kind, payload, at, by, evidence in src.execute(
            "SELECT seq, entity_id, entity_type, kind, payload, at, by, evidence FROM events ORDER BY seq"
        ):
            fields = (schemas.get(etype) or {}).get("fields") or {}
            p = json.loads(payload)
            if kind == "status_changed" and isinstance(p, dict) and "to" in p:
                change = "-> " + str(fmt.value(fields.get("status"), p["to"]))
            elif kind == "corrected" and isinstance(p, dict) and isinstance(p.get("payload"), dict):
                change = "corrected: " + fmt.changes(fields, p["payload"])
            elif isinstance(p, dict):
                change = fmt.changes(fields, p)
            else:
                change = json.dumps(p, ensure_ascii=False)
            history.append((seq, etype, eid, names.get(eid), kind, at, by, change, evidence))

        dst.execute("CREATE TABLE _history (seq, type, id, display_name, kind, at, by, change, evidence)")
        dst.executemany("INSERT INTO _history VALUES (?,?,?,?,?,?,?,?,?)", history)
        dst.execute("CREATE TABLE _fields (type, field, value_type, ref, labels, required, description, source)")
        dst.executemany("INSERT INTO _fields VALUES (?,?,?,?,?,?,?,?)", field_rows)
        dst.execute("CREATE TABLE _summary (type, kind, records, retracted, last_updated, description)")
        dst.executemany("INSERT INTO _summary VALUES (?,?,?,?,?,?)", summary_rows)
        dst.execute("CREATE TABLE _meta (key, value)")
        dst.executemany(
            "INSERT INTO _meta VALUES (?,?)",
            [
                ("source", str(source.resolve())),
                ("source_modified", _mtime_text(_source_mtime(source))),
                ("built_at", datetime.now().isoformat(timespec="seconds")),
            ],
        )
        dst.commit()
        dst.close()
    finally:
        src.close()

    try:
        os.replace(tmp, out)
        return out
    except PermissionError:
        alt = out.with_name(f"{out.stem}-{datetime.now():%Y%m%d-%H%M%S}{out.suffix}")
        os.replace(tmp, alt)
        return alt


# --------------------------------------------------------------------------- discovery


@dataclass
class DbEntry:
    path: Path
    kind: str  # "configured" | "local" | "scanned"
    sources: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    def view_dir(self) -> Path:
        return self.path.parent / "views"

    def view_path(self) -> Path:
        return self.view_dir() / f"{self.name}-view.db"

    def latest_view(self) -> Path | None:
        found = sorted(self.view_dir().glob(f"{self.name}-view*.db"), key=lambda p: p.stat().st_mtime)
        return found[-1] if found else None

    def view_status(self) -> str:
        view = self.latest_view()
        if view is None:
            return "-"
        built = view.stat().st_mtime
        text = _mtime_text(built, minutes=True)
        if self.exists and _source_mtime(self.path) > built:
            text += " (stale)"
        return text


def _source_mtime(path: Path) -> float:
    """A WAL database changes in <db>-wal first, so take the later of the two."""
    times = [p.stat().st_mtime for p in (path, Path(str(path) + "-wal")) if p.is_file()]
    return max(times) if times else 0.0


def _mtime_text(t: float, minutes: bool = False) -> str:
    return datetime.fromtimestamp(t).strftime("%Y-%m-%d %H:%M" if minutes else "%Y-%m-%dT%H:%M:%S")


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _servers(block: Any) -> Iterable[tuple[str, str]]:
    if not isinstance(block, dict):
        return
    for name, server in block.items():
        env = server.get("env") if isinstance(server, dict) else None
        if isinstance(env, dict) and isinstance(env.get("MNEMENTO_DB"), str):
            yield name, env["MNEMENTO_DB"]


def desktop_config_path(home: Path) -> Path:
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "Claude" / "claude_desktop_config.json"
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Claude" / "claude_desktop_config.json"
    return home / ".config" / "Claude" / "claude_desktop_config.json"


def is_mnemento_db(path: Path) -> bool:
    try:
        con = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)
        try:
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()
    except sqlite3.Error:
        return False
    return REQUIRED_TABLES <= tables


def find_databases(
    *, home: Path | None = None, cwd: Path | None = None, scan: Iterable[Path] = ()
) -> list[DbEntry]:
    """Databases named by MNEMENTO_DB in Claude Code / Claude Desktop MCP configs and the
    environment, *.db files directly in ~/.mnemento, and Mnemento databases under ``scan``."""
    home = home or Path.home()
    cwd = cwd or Path.cwd()
    found: dict[str, DbEntry] = {}

    def add(raw: str | Path, kind: str, source: str) -> None:
        path = Path(os.path.expanduser(str(raw))).resolve()
        key = os.path.normcase(str(path))
        entry = found.setdefault(key, DbEntry(path, kind))
        if source not in entry.sources:
            entry.sources.append(source)

    claude = _read_json(home / ".claude.json")
    if isinstance(claude, dict):
        for name, db in _servers(claude.get("mcpServers")):
            add(db, "configured", f"{name} @ user")
        projects = claude.get("projects")
        if isinstance(projects, dict):
            for project, conf in projects.items():
                if isinstance(conf, dict):
                    for name, db in _servers(conf.get("mcpServers")):
                        add(db, "configured", f"{name} @ {Path(project).name}")
    for cfg, label in ((cwd / ".mcp.json", ".mcp.json"), (desktop_config_path(home), "Claude Desktop")):
        data = _read_json(cfg)
        if isinstance(data, dict):
            for name, db in _servers(data.get("mcpServers")):
                add(db, "configured", f"{name} @ {label}")
    if os.environ.get("MNEMENTO_DB"):
        add(os.environ["MNEMENTO_DB"], "configured", "MNEMENTO_DB")
    local = home / ".mnemento"
    if local.is_dir():
        for p in sorted(local.glob("*.db")):
            add(p, "local", "~/.mnemento")
    for root in scan:
        for p in sorted(Path(root).expanduser().rglob("*.db")):
            if p.parent.name != "views" and is_mnemento_db(p):
                add(p, "scanned", str(root))

    order = {"configured": 0, "local": 1, "scanned": 2}
    return sorted(
        found.values(),
        key=lambda e: (order[e.kind], -(e.path.stat().st_mtime if e.exists else 0)),
    )


def select(entries: list[DbEntry], query: str) -> list[DbEntry]:
    """By list number, existing path, exact name, then partial name / source / path."""
    if query.isdigit():
        i = int(query)
        return [entries[i - 1]] if 1 <= i <= len(entries) else []
    p = Path(query).expanduser()
    if p.is_file():
        match = [e for e in entries if os.path.normcase(str(e.path)) == os.path.normcase(str(p.resolve()))]
        return match or [DbEntry(p.resolve(), "path", ["argument"])]
    exact = [e for e in entries if e.name == query]
    if exact:
        return exact
    q = query.lower()
    return [e for e in entries if q in e.name.lower() or q in " ".join(e.sources).lower() or q in str(e.path).lower()]


def open_in_viewer(paths: list[Path]) -> None:
    """VS Code when its `code` command exists (pair it with a SQLite viewer extension),
    otherwise the operating system's default application for .db files."""
    code = shutil.which("code")
    if code:
        subprocess.run([code, *map(str, paths)], check=False)
        return
    for p in paths:
        if sys.platform == "win32":
            os.startfile(p)  # type: ignore[attr-defined]
        else:
            subprocess.run(["open" if sys.platform == "darwin" else "xdg-open", str(p)], check=False)


# --------------------------------------------------------------------------- CLI


def _table(entries: list[DbEntry]) -> str:
    rows = [("#", "kind", "name", "size_kb", "modified", "view", "used_by", "path")]
    for i, e in enumerate(entries, 1):
        rows.append((
            str(i), e.kind, e.name,
            str(round(e.path.stat().st_size / 1024)) if e.exists else "",
            _mtime_text(e.path.stat().st_mtime, minutes=True) if e.exists else "MISSING",
            e.view_status(), "; ".join(e.sources), str(e.path),
        ))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return "\n".join("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows)


def _choose(entries: list[DbEntry], query: str | None) -> list[DbEntry]:
    if query:  # a path works even when nothing was discovered

        chosen = select(entries, query)
        if not chosen:
            raise SystemExit(f"No database matches {query!r}. See `mnemento-db list`.")
        if len(chosen) > 1:
            raise SystemExit(f"{query!r} matches several databases; give the number:\n" + _table(chosen))
        return chosen
    if not entries:
        raise SystemExit("No Mnemento databases found (no MNEMENTO_DB in MCP configs, nothing in ~/.mnemento).")
    if len(entries) == 1:
        return entries
    print(_table(entries))
    if not sys.stdin.isatty():
        raise SystemExit("Several databases; name one (number, name or path).")
    return _choose(entries, input("number: ").strip())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mnemento-db", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    for cmd, text in (
        ("list", "list Mnemento databases"),
        ("open", "open a raw database in a viewer"),
        ("view", "build the readable view of a database and open it"),
    ):
        p = sub.add_parser(cmd, help=text)
        if cmd != "list":
            p.add_argument("db", nargs="?", help="list number, name or path")
            p.add_argument("--no-open", action="store_true", help="do not start a viewer")
        p.add_argument("--scan", action="append", default=[], type=Path,
                       help="also find Mnemento databases under this folder (repeatable)")
    if cmd_view := sub.choices.get("view"):
        cmd_view.add_argument("--out", type=Path, help="view file (default <db dir>/views/<name>-view.db)")
    args = ap.parse_args(argv)

    entries = find_databases(scan=args.scan)
    if args.cmd == "list":
        print(_table(entries) if entries else "No Mnemento databases found.")
        return 0

    chosen = [e for e in _choose(entries, args.db) if e.exists]
    if not chosen:
        raise SystemExit("The database file does not exist.")
    paths = [e.path for e in chosen]
    if args.cmd == "view":
        built = []
        for e in chosen:
            out = build_view(e.path, args.out or e.view_path())
            print(f"Built view: {out} (from {e.path})")
            built.append(out)
        paths = built
    if not args.no_open:
        open_in_viewer(paths)
        for p in paths:
            print(f"Opened: {p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
