from __future__ import annotations

from ductor_bot.workspace.path_aliases import AliasRegistration, PathAliasRegistry
from ductor_bot.workspace.paths import DuctorPaths


def test_registry_upsert_persists_and_syncs_memory(tmp_path) -> None:
    home = tmp_path / "home"
    workspace = home / "workspace" / "memory_system"
    workspace.mkdir(parents=True)
    (workspace / "MAINMEMORY.md").write_text("# Main Memory\n", encoding="utf-8")
    (home / "SHAREDMEMORY.md").write_text("# Shared Memory\n", encoding="utf-8")
    paths = DuctorPaths(ductor_home=home)
    registry = PathAliasRegistry(paths, agent_name="main")

    entry = registry.upsert(
        AliasRegistration(
            alias="hk",
            path="zhihaol/100_Project/2604_HK",
            purpose="香港就业机会与优才计划",
        )
    )

    loaded = registry.get("hk")
    assert loaded is not None
    assert loaded.path == "zhihaol/100_Project/2604_HK"
    assert loaded.purpose == "香港就业机会与优才计划"
    assert entry.updated_by == "main"

    shared_text = paths.sharedmemory_path.read_text(encoding="utf-8")
    main_text = paths.mainmemory_path.read_text(encoding="utf-8")
    assert "@hk: zhihaol/100_Project/2604_HK" in shared_text
    assert "purpose: 香港就业机会与优才计划" in shared_text
    assert "@hk: zhihaol/100_Project/2604_HK" in main_text


def test_registry_migrates_legacy_memory_lines(tmp_path) -> None:
    home = tmp_path / "home"
    memory_dir = home / "workspace" / "memory_system"
    memory_dir.mkdir(parents=True)
    (memory_dir / "MAINMEMORY.md").write_text(
        "# Main Memory\n\n- @hk -> zhihaol/100_Project/2604_HK | purpose: 香港优才计划\n",
        encoding="utf-8",
    )
    (home / "SHAREDMEMORY.md").write_text("# Shared Memory\n", encoding="utf-8")
    paths = DuctorPaths(ductor_home=home)
    registry = PathAliasRegistry(paths)

    imported = registry.migrate_legacy_memory_aliases()

    assert imported == 1
    entry = registry.get("hk")
    assert entry is not None
    assert entry.path == "zhihaol/100_Project/2604_HK"
    assert entry.purpose == "香港优才计划"


def test_registry_migrates_semantic_path_table_and_normalizes_mount(tmp_path) -> None:
    home = tmp_path / "home"
    memory_dir = home / "workspace" / "memory_system"
    config_dir = home / "config"
    memory_dir.mkdir(parents=True)
    config_dir.mkdir(parents=True)
    (config_dir / "config.json").write_text(
        '{"docker":{"mounts":["D:\\\\Documents\\\\zhihaol","D:\\\\Documents\\\\GitHub"]}}',
        encoding="utf-8",
    )
    (memory_dir / "MAINMEMORY.md").write_text(
        "# Main Memory\n\n"
        "## Semantic Path Registry (V1.0)\n\n"
        "| Alias | Physical Path | Description |\n"
        "| :--- | :--- | :--- |\n"
        "| `@vault` | `/mnt/zhihaol` | Obsidian 知识库根目录 |\n"
        "| `@projects` | `/mnt/zhihaol/100_Project` | 活跃项目文档区 |\n"
        "| `@alpha` | `/mnt/GitHub/alpha_engine` | 量化研究工作台核心库 |\n"
        "| `@p-lifeos` | `@projects/2603_LifeOS` | LifeOS 迭代与维护文档 |\n",
        encoding="utf-8",
    )
    (home / "SHAREDMEMORY.md").write_text("# Shared Memory\n", encoding="utf-8")
    paths = DuctorPaths(ductor_home=home)
    registry = PathAliasRegistry(paths)

    imported = registry.migrate_legacy_memory_aliases()

    assert imported == 4
    vault = registry.get("vault")
    assert vault is not None
    assert vault.path == "/mnt/zhihaol"
    assert vault.host_path.endswith("Documents\\zhihaol")
    assert vault.container_path == "/mnt/zhihaol"
    project = registry.get("p-lifeos")
    assert project is not None
    assert project.path == "/mnt/zhihaol/100_Project/2603_LifeOS"
    assert project.host_path.endswith("Documents\\zhihaol\\100_Project\\2603_LifeOS")
    assert project.container_path == "/mnt/zhihaol/100_Project/2603_LifeOS"


def test_registry_sanitize_removes_invalid_entries(tmp_path) -> None:
    home = tmp_path / "home"
    home.mkdir(parents=True)
    paths = DuctorPaths(ductor_home=home)
    paths.path_aliases_path.write_text(
        '{'
        '"bot4":{"alias":"bot4","path":"assistant):"},'
        '"p":{"alias":"p","path":"esg"},'
        '"hk":{"alias":"hk","path":"zhihaol/100_Project/2604_HK"}'
        '}',
        encoding="utf-8",
    )
    registry = PathAliasRegistry(paths)

    removed = registry.sanitize()

    assert removed == 2
    assert registry.get("hk") is not None
    assert registry.get("bot4") is None


def test_parse_registration_supports_cn_command(tmp_path) -> None:
    registry = PathAliasRegistry(DuctorPaths(ductor_home=tmp_path / "home"))

    parsed = registry.parse_registration(
        "请帮我把 @hk 指向 zhihaol/100_Project/2604_HK 路径,这个路径用于讨论香港的就业机会,香港优才计划"
    )

    assert parsed is not None
    assert parsed.alias == "hk"
    assert parsed.path == "zhihaol/100_Project/2604_HK"
    assert "香港的就业机会" in parsed.purpose


def test_ensure_canonical_project_aliases_creates_p_prefix(tmp_path) -> None:
    home = tmp_path / "home"
    workspace = home / "workspace" / "memory_system"
    config_dir = home / "config"
    workspace.mkdir(parents=True)
    config_dir.mkdir(parents=True)
    (workspace / "MAINMEMORY.md").write_text("# Main Memory\n", encoding="utf-8")
    (home / "SHAREDMEMORY.md").write_text("# Shared Memory\n", encoding="utf-8")
    (config_dir / "config.json").write_text(
        '{"docker":{"mounts":["D:\\\\Documents\\\\zhihaol"]}}',
        encoding="utf-8",
    )
    paths = DuctorPaths(ductor_home=home)
    registry = PathAliasRegistry(paths, agent_name="main")
    registry.upsert(
        AliasRegistration(
            alias="hk",
            path="zhihaol/100_Project/2604_HK",
            purpose="香港就业机会与优才计划",
        )
    )

    created = registry.ensure_canonical_project_aliases()

    assert created == 1
    canonical = registry.get("p-hk")
    assert canonical is not None
    assert canonical.path == "/mnt/zhihaol/100_Project/2604_HK"
    assert canonical.host_path.endswith("Documents\\zhihaol\\100_Project\\2604_HK")
