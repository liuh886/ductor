"""Path alias registry and compatibility helpers."""

# ruff: noqa: RUF001

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from ductor_bot.infra.json_store import atomic_json_save, load_json
from ductor_bot.workspace.paths import DuctorPaths

logger = logging.getLogger(__name__)

_ALIAS_MENTION_RE = re.compile(r"(?<![\w/])@([a-zA-Z][a-zA-Z0-9_-]{0,63})")
_ALIAS_LINE_RE = re.compile(
    r"^\s*-\s*@(?P<alias>[a-zA-Z][a-zA-Z0-9_-]{0,63})\s*:\s*"
    r"(?P<path>[^|]+?)(?:\s+\|\s+purpose:\s*(?P<purpose>.+))?\s*$",
    re.IGNORECASE,
)
_LEGACY_ALIAS_RE = re.compile(
    r"@(?P<alias>[a-zA-Z][a-zA-Z0-9_-]{0,63})\s*"
    r"(?:[:=-]>?|指向|映射到|绑定到|路径为|path\s*(?:is|=|->)?)\s*"
    r"(?P<path>[^\s|,，。；;]+)"
    r"(?:.*?(?:purpose|用途|用于|用来)[:：]?\s*(?P<purpose>.+))?",
    re.IGNORECASE,
)
_TABLE_ALIAS_RE = re.compile(
    r"^\|\s*`?(?P<alias>@[a-zA-Z][a-zA-Z0-9_-]{0,63})`?\s*\|\s*"
    r"`?(?P<path>[^|`]+)`?\s*\|\s*(?P<purpose>[^|]+?)\s*\|?\s*$"
)
_REGISTER_PATTERNS = (
    re.compile(
        r"(?:请帮我|帮我|请)?(?:把|将)\s*@(?P<alias>[a-zA-Z][a-zA-Z0-9_-]{0,63})\s*"
        r"(?:指向|映射到|绑定到|设为|设置为)\s*"
        r"(?P<path>[^\s,，。；;]+)"
        r"(?:\s*(?:路径|目录))?"
        r"(?P<tail>.*)$",
        re.IGNORECASE,
    ),
    re.compile(
        r"(?:register|set|map|bind)\s*@(?P<alias>[a-zA-Z][a-zA-Z0-9_-]{0,63})\s*"
        r"(?:to|->|as)\s*"
        r"(?P<path>[^\s,，。；;]+)"
        r"(?P<tail>.*)$",
        re.IGNORECASE,
    ),
)


@dataclass(frozen=True, slots=True)
class PathAliasEntry:
    """One persisted path alias."""

    alias: str
    path: str
    purpose: str = ""
    domain: str = ""
    north_star: str = ""
    quality_bar: str = ""
    owner_bot: str = ""
    host_path: str = ""
    container_path: str = ""
    created_at: str = ""
    updated_at: str = ""
    source: str = "registry"
    updated_by: str = "main"


@dataclass(frozen=True, slots=True)
class AliasRegistration:
    """Parsed user intent to create/update one alias."""

    alias: str
    path: str
    purpose: str = ""


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalize_alias(alias: str) -> str:
    return alias.lower().lstrip("@")


def _clean_path(raw: str) -> str:
    return raw.strip().strip("`'\"").rstrip("，。；;,.")


def _clean_purpose(raw: str) -> str:
    purpose = raw.strip()
    purpose = re.sub(
        r"^(?:[,，。；;\s]*(?:这个路径|该路径|这个目录|该目录))?\s*"
        r"(?:用于|用来|for|used\s+for|purpose\s+is)\s*",
        "",
        purpose,
        flags=re.IGNORECASE,
    )
    return purpose.strip(" `'\"，。；;,.")


def _extract_purpose(tail: str) -> str:
    if not tail.strip():
        return ""
    match = re.search(
        r"(?:用途|用于|用来|for|used\s+for|purpose\s+is)\s*(?P<purpose>.+)$",
        tail,
        flags=re.IGNORECASE,
    )
    if match:
        return _clean_purpose(match.group("purpose"))
    return _clean_purpose(tail)


def _parse_alias_line(line: str) -> PathAliasEntry | None:
    match = _TABLE_ALIAS_RE.match(line)
    if not match:
        match = _ALIAS_LINE_RE.match(line)
    if not match:
        match = _LEGACY_ALIAS_RE.search(line)
        if not match:
            return None
    alias = _normalize_alias(match.group("alias"))
    path = _clean_path(match.group("path"))
    purpose = _clean_purpose(match.group("purpose") or "")
    if not alias or not path:
        return None
    now = _now_iso()
    return PathAliasEntry(
        alias=alias,
        path=path,
        purpose=purpose,
        created_at=now,
        updated_at=now,
        source="memory",
        updated_by="migration",
    )


def _render_alias_line(entry: PathAliasEntry) -> str:
    line = f"- @{entry.alias}: {entry.path}"
    if entry.purpose:
        line += f" | purpose: {entry.purpose}"
    return line


def _upsert_markdown_alias(path: Path, heading: str, entry: PathAliasEntry) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = path.read_text(encoding="utf-8") if path.exists() else ""
    lines = text.splitlines()

    try:
        heading_index = lines.index(heading)
    except ValueError:
        if lines and lines[-1].strip():
            lines.append("")
        lines.extend((heading, _render_alias_line(entry)))
        path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
        return

    insert_at = heading_index + 1
    while insert_at < len(lines) and not lines[insert_at].startswith("## "):
        existing = _parse_alias_line(lines[insert_at])
        if existing is not None and existing.alias == entry.alias:
            lines[insert_at] = _render_alias_line(entry)
            path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
            return
        insert_at += 1

    lines.insert(insert_at, _render_alias_line(entry))
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


class PathAliasRegistry:
    """Stable global alias registry with memory compatibility."""

    def __init__(self, paths: DuctorPaths, *, agent_name: str = "main") -> None:
        self._paths = paths
        self._agent_name = agent_name
        self._path = paths.path_aliases_path
        self._mounts = self._load_mounts()

    @property
    def path(self) -> Path:
        return self._path

    def load(self) -> dict[str, PathAliasEntry]:
        raw = load_json(self._path)
        if not isinstance(raw, dict):
            return {}
        out: dict[str, PathAliasEntry] = {}
        for alias, payload in raw.items():
            if not isinstance(payload, dict):
                continue
            normalized = _normalize_alias(alias)
            path = _clean_path(str(payload.get("path", "") or ""))
            if not normalized or not self._is_valid_path_value(path):
                continue
            container_path = _clean_path(str(payload.get("container_path", "") or ""))
            host_path = _clean_path(str(payload.get("host_path", "") or ""))
            normalized_entry = self._normalize_entry(
                PathAliasEntry(
                    alias=normalized,
                    path=path,
                    purpose=_clean_purpose(str(payload.get("purpose", "") or "")),
                    domain=_clean_purpose(str(payload.get("domain", "") or "")),
                    north_star=_clean_purpose(str(payload.get("north_star", "") or "")),
                    quality_bar=_clean_purpose(str(payload.get("quality_bar", "") or "")),
                    owner_bot=_clean_purpose(str(payload.get("owner_bot", "") or "")),
                    host_path=host_path,
                    container_path=container_path,
                    created_at=str(payload.get("created_at", "") or ""),
                    updated_at=str(payload.get("updated_at", "") or ""),
                    source=str(payload.get("source", "registry") or "registry"),
                    updated_by=str(payload.get("updated_by", self._agent_name) or self._agent_name),
                )
            )
            if not self._is_valid_path_value(normalized_entry.path):
                continue
            out[normalized] = normalized_entry
        if out:
            out = self._resolve_alias_entries(out)
        return out

    def save(self, aliases: dict[str, PathAliasEntry]) -> None:
        payload = {alias: asdict(entry) for alias, entry in sorted(aliases.items())}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json_save(self._path, payload)

    def get(self, alias: str) -> PathAliasEntry | None:
        return self.load().get(_normalize_alias(alias))

    def has(self, alias: str) -> bool:
        return self.get(alias) is not None

    def mentions(self, text: str) -> list[PathAliasEntry]:
        aliases = self.load()
        seen: set[str] = set()
        out: list[PathAliasEntry] = []
        for match in _ALIAS_MENTION_RE.finditer(text):
            alias = _normalize_alias(match.group(1))
            if alias in seen:
                continue
            entry = aliases.get(alias)
            if entry is None:
                continue
            seen.add(alias)
            out.append(entry)
        return out

    def render_context_for_text(self, text: str) -> str:
        entries = self.mentions(text)
        if not entries:
            return ""
        lines = ["## Path Alias Context"]
        for entry in entries:
            line = f"- @{entry.alias}: canonical `{entry.path}`"
            if entry.container_path:
                line += f" | docker `{entry.container_path}`"
            if entry.host_path:
                line += f" | host `{entry.host_path}`"
            if entry.purpose:
                line += f" | {entry.purpose}"
            if entry.north_star:
                line += f" | north_star: {entry.north_star}"
            if entry.owner_bot:
                line += f" | owner_bot: {entry.owner_bot}"
            lines.append(line)
        lines.append(
            "- If you are running inside Docker, prefer the `docker` path. If you are running on the host, prefer the `host` path."
        )
        return "\n".join(lines)

    def parse_registration(self, text: str) -> AliasRegistration | None:
        stripped = text.strip()
        for pattern in _REGISTER_PATTERNS:
            match = pattern.search(stripped)
            if not match:
                continue
            alias = _normalize_alias(match.group("alias"))
            path = _clean_path(match.group("path"))
            purpose = _extract_purpose(match.group("tail") or "")
            if alias and path:
                return AliasRegistration(alias=alias, path=path, purpose=purpose)
        return None

    def upsert(self, registration: AliasRegistration, *, source: str = "registry") -> PathAliasEntry:
        aliases = self.load()
        current = aliases.get(registration.alias)
        now = _now_iso()
        entry = self._resolve_entry_alias_refs(
            self._normalize_entry(
                PathAliasEntry(
                    alias=registration.alias,
                    path=registration.path,
                    purpose=registration.purpose,
                    domain=current.domain if current else "",
                    north_star=current.north_star if current else "",
                    quality_bar=current.quality_bar if current else "",
                    owner_bot=current.owner_bot if current else "",
                    host_path=current.host_path if current else "",
                    container_path=current.container_path if current else "",
                    created_at=current.created_at if current and current.created_at else now,
                    updated_at=now,
                    source=source,
                    updated_by=self._agent_name,
                )
            ),
            aliases,
        )
        aliases[entry.alias] = entry
        self.save(aliases)
        self._sync_memory(entry)
        return entry

    def migrate_legacy_memory_aliases(self) -> int:
        aliases = self.load()
        imported = 0
        for source_path in (self._paths.sharedmemory_path, self._paths.mainmemory_path):
            if not source_path.exists():
                continue
            for line in source_path.read_text(encoding="utf-8").splitlines():
                entry = _parse_alias_line(line)
                if entry is None or entry.alias in aliases:
                    continue
                normalized = self._normalize_entry(entry)
                if not self._is_valid_path_value(normalized.path):
                    continue
                aliases[normalized.alias] = normalized
                imported += 1
        if imported:
            aliases = self._resolve_alias_entries(aliases)
        if imported:
            logger.info("Imported %d legacy path alias(es) into %s", imported, self._path)
            self.save(aliases)
        return imported

    def _sync_memory(self, entry: PathAliasEntry) -> None:
        _upsert_markdown_alias(self._paths.sharedmemory_path, "## Path Aliases", entry)
        _upsert_markdown_alias(self._paths.mainmemory_path, "## Path Aliases", entry)

    def sanitize(self) -> int:
        raw = load_json(self._path)
        if not isinstance(raw, dict):
            return 0
        cleaned = self.load()
        removed = len(raw) - len(cleaned)
        if removed:
            self.save(cleaned)
        return removed

    def ensure_canonical_project_aliases(self) -> int:
        aliases = self.load()
        created = 0
        additions: list[PathAliasEntry] = []
        for alias, entry in aliases.items():
            if alias.startswith("p-"):
                continue
            canonical = self._canonical_project_alias(alias, entry)
            if canonical is None or canonical in aliases:
                continue
            now = _now_iso()
            additions.append(
                PathAliasEntry(
                    alias=canonical,
                    path=entry.path,
                    purpose=entry.purpose,
                    domain=entry.domain,
                    north_star=entry.north_star,
                    quality_bar=entry.quality_bar,
                    owner_bot=entry.owner_bot,
                    host_path=entry.host_path,
                    container_path=entry.container_path,
                    created_at=now,
                    updated_at=now,
                    source="canonicalized",
                    updated_by=self._agent_name,
                )
            )
        for entry in additions:
            aliases[entry.alias] = entry
            self._sync_memory(entry)
            created += 1
        if created:
            self.save(aliases)
        return created

    def _load_mounts(self) -> tuple[tuple[Path, str], ...]:
        from ductor_bot.infra.docker import resolve_mount_target

        raw = load_json(self._paths.config_path)
        if not isinstance(raw, dict):
            return ()
        docker = raw.get("docker", {})
        if not isinstance(docker, dict):
            return ()
        mounts = docker.get("mounts", [])
        if not isinstance(mounts, list):
            return ()
        used: set[str] = set()
        resolved: list[tuple[Path, str]] = []
        for item in mounts:
            if not isinstance(item, str):
                continue
            pair = resolve_mount_target(item, used)
            if pair is None:
                continue
            resolved.append(pair)
        return tuple(resolved)

    @staticmethod
    def _is_valid_path_value(path: str) -> bool:
        return bool(path) and ("/" in path or "\\" in path or path.startswith("@"))

    def _normalize_entry(self, entry: PathAliasEntry) -> PathAliasEntry:
        canonical = entry.path
        host_path = entry.host_path
        container_path = entry.container_path

        if canonical.startswith("/mnt/"):
            container_path = canonical
            host_guess = self._host_from_container(canonical)
            if host_guess:
                host_path = host_guess
        elif canonical.startswith("@"):
            pass
        else:
            mounted = self._mount_relative_to_container(canonical)
            if mounted is not None:
                host_path, container_path = mounted
                canonical = container_path

        if not canonical and container_path:
            canonical = container_path
        if not canonical and host_path:
            canonical = host_path

        return PathAliasEntry(
            alias=entry.alias,
            path=canonical,
            purpose=entry.purpose,
            domain=entry.domain,
            north_star=entry.north_star or self._default_north_star(entry),
            quality_bar=entry.quality_bar or self._default_quality_bar(entry),
            owner_bot=entry.owner_bot,
            host_path=host_path,
            container_path=container_path,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
            source=entry.source,
            updated_by=entry.updated_by,
        )

    def _resolve_alias_entries(self, aliases: dict[str, PathAliasEntry]) -> dict[str, PathAliasEntry]:
        resolved: dict[str, PathAliasEntry] = {}
        for alias, entry in aliases.items():
            resolved[alias] = self._resolve_entry_alias_refs(entry, aliases)
        return resolved

    def _resolve_entry_alias_refs(
        self,
        entry: PathAliasEntry,
        aliases: dict[str, PathAliasEntry],
    ) -> PathAliasEntry:
        if not entry.path.startswith("@"):
            return entry
        seen = {entry.alias}
        current_path = entry.path
        host_path = entry.host_path
        container_path = entry.container_path
        while current_path.startswith("@"):
            resolved = self._resolve_alias_reference(current_path, aliases, seen)
            if resolved is None:
                break
            current_path, host_path, container_path = resolved
        return PathAliasEntry(
            alias=entry.alias,
            path=current_path,
            purpose=entry.purpose,
            domain=entry.domain,
            north_star=entry.north_star,
            quality_bar=entry.quality_bar,
            owner_bot=entry.owner_bot,
            host_path=host_path,
            container_path=container_path,
            created_at=entry.created_at,
            updated_at=entry.updated_at,
            source=entry.source,
            updated_by=entry.updated_by,
        )

    def _resolve_alias_reference(
        self,
        path: str,
        aliases: dict[str, PathAliasEntry],
        seen: set[str],
    ) -> tuple[str, str, str] | None:
        match = re.match(r"^@(?P<alias>[a-zA-Z][a-zA-Z0-9_-]{0,63})(?P<suffix>/.*)?$", path)
        if not match:
            return None
        alias = _normalize_alias(match.group("alias"))
        if alias in seen:
            return None
        base = aliases.get(alias)
        if base is None:
            return None
        seen.add(alias)
        suffix = (match.group("suffix") or "").lstrip("/")
        base_path = base.path
        base_host = base.host_path
        base_container = base.container_path
        if base_path.startswith("@"):
            nested = self._resolve_alias_reference(base_path, aliases, seen)
            if nested is None:
                return None
            base_path, base_host, base_container = nested
        return (
            self._join_alias_path(base_path, suffix),
            self._join_alias_path(base_host, suffix),
            self._join_alias_path(base_container, suffix),
        )

    @staticmethod
    def _join_alias_path(base: str, suffix: str) -> str:
        if not base:
            return ""
        if not suffix:
            return base
        if "/" in base:
            return f"{base.rstrip('/')}/{suffix}"
        return str(Path(base) / Path(*suffix.split("/")))

    def _host_from_container(self, container_path: str) -> str:
        for host_root, container_root in self._mounts:
            if container_path == container_root or container_path.startswith(container_root + "/"):
                suffix = container_path[len(container_root) :].lstrip("/")
                host_target = host_root / Path(*suffix.split("/")) if suffix else host_root
                return str(host_target)
        return ""

    def _mount_relative_to_container(self, raw_path: str) -> tuple[str, str] | None:
        normalized = raw_path.replace("\\", "/").lstrip("/")
        for host_root, container_root in self._mounts:
            mount_name = Path(container_root).name.lower()
            if normalized.lower() == mount_name:
                return str(host_root), container_root
            prefix = f"{mount_name}/"
            if normalized.lower().startswith(prefix):
                suffix = normalized[len(prefix) :]
                host_target = host_root / Path(*suffix.split("/")) if suffix else host_root
                return str(host_target), f"{container_root}/{suffix}" if suffix else container_root
        return None

    @staticmethod
    def _canonical_project_alias(alias: str, entry: PathAliasEntry) -> str | None:
        normalized_path = entry.path.replace("\\", "/")
        if "/100_Project/" not in normalized_path:
            return None
        return f"p-{alias}"

    @staticmethod
    def _default_north_star(entry: PathAliasEntry) -> str:
        if entry.purpose:
            return f"Deliver durable progress for {entry.purpose}."
        return f"Keep @{entry.alias} aligned with its project objective."

    @staticmethod
    def _default_quality_bar(entry: PathAliasEntry) -> str:
        normalized_path = entry.path.replace("\\", "/")
        if "/100_Project/" in normalized_path:
            return (
                "Complex work should maintain DESIGN.md, TASKS.md, and EVALUATE.md "
                "before major execution."
            )
        return "Preserve path purpose, keep outputs structured, and record meaningful follow-through."
