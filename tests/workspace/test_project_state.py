from __future__ import annotations

from ductor_bot.workspace.project_state import ensure_project_state, load_project_state


def test_ensure_project_state_upgrades_existing_design(tmp_path) -> None:
    root = tmp_path / "2604_HK"
    root.mkdir(parents=True)
    design = root / "DESIGN.md"
    design.write_text(
        "# Project Design\n\nThis project discusses Hong Kong opportunities.\n",
        encoding="utf-8",
    )

    context = ensure_project_state(
        root_path=root,
        alias="p-hk",
        purpose="香港就业机会与优才计划",
        owner_bot="bot3-writer",
    )

    assert context is not None
    text = design.read_text(encoding="utf-8")
    assert text.startswith("---\n")
    assert "alias: p-hk" in text
    assert "purpose: 香港就业机会与优才计划" in text
    assert "owner_bot: bot3-writer" in text
    assert "# Project Design" in text
    assert (root / "TASKS.md").exists()
    assert (root / "EVALUATE.md").exists()


def test_load_project_state_reads_frontmatter(tmp_path) -> None:
    root = tmp_path / "2601_ESG30"
    root.mkdir(parents=True)
    (root / "DESIGN.md").write_text(
        "---\n"
        "alias: p-esg\n"
        "purpose: CCUS 数字 MRV 与 ESG30 研究\n"
        "north_star: Produce a durable CCUS dMRV research program.\n"
        "owner_bot: ccus-bot\n"
        "domain: research-policy\n"
        "quality_bar:\n"
        "  - Sources must be traceable.\n"
        "  - Risks must be explicit.\n"
        "requires_design: true\n"
        "requires_plan: true\n"
        "requires_evaluation: true\n"
        "---\n\n"
        "# DESIGN\n",
        encoding="utf-8",
    )

    context = load_project_state(root_path=root)

    assert context is not None
    assert context.alias == "p-esg"
    assert context.owner_bot == "ccus-bot"
    assert context.domain == "research-policy"
    assert context.quality_bar == ("Sources must be traceable.", "Risks must be explicit.")


def test_ensure_project_state_prefers_canonical_project_alias(tmp_path) -> None:
    root = tmp_path / "2604_HK"
    root.mkdir(parents=True)
    (root / "DESIGN.md").write_text(
        "---\n"
        "alias: hk\n"
        "purpose: 香港就业机会与优才计划\n"
        "---\n\n"
        "# DESIGN\n",
        encoding="utf-8",
    )

    ensure_project_state(
        root_path=root,
        alias="p-hk",
        purpose="香港就业机会与优才计划",
    )

    text = (root / "DESIGN.md").read_text(encoding="utf-8")
    assert "alias: p-hk" in text
