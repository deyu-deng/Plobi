"""Word-shape contracts for scripts/rename_plobi.py (R-038 rename codemod).

The codemod is the only safe way to move ~74k identifier occurrences, so its
rule engine is tested as a pure function: which shapes rewrite, which survive,
and that a second pass changes nothing (idempotence is what makes a re-run after
a partial slice safe).

The "must survive" rows are the ones a blanket sed would get wrong: other
people's repo names and handles, third-party model identifiers, contributor
e-mail domains, and upstream attribution links.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
_spec = importlib.util.spec_from_file_location(
    "rename_plobi", REPO / "scripts" / "rename_plobi.py"
)
assert _spec and _spec.loader
rename_plobi = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rename_plobi)

REWRITE = [
    # separators and case families
    ("hermes_cli", "plobi_cli"),
    (".hermes", ".plobi"),
    ("HERMES_HOME", "PLOBI_HOME"),
    ("hermes-agent", "plobi-agent"),
    ("HermesCLI", "PlobiCLI"),
    ("VaelisL1", "PlobiL1"),
    ("currentHermesSessionId", "currentPlobiSessionId"),
    ("startHermes()", "startPlobi()"),
    ("Hermes Agent", "Plobi Agent"),
    ("vaelis_secretary_ask", "plobi_secretary_ask"),
    ("VAELIS_DELEGATION_GLOBAL_MAX", "PLOBI_DELEGATION_GLOBAL_MAX"),
    ("com.vaelis.desktop", "com.plobi.desktop"),
]

PRESERVE = [
    # other people's projects and handles: glued lowercase, not our identifiers
    "AaronWong1999/hermesclaw",
    "r/hermesagent",
    "hermesagent26@gmail.com",
    # third-party model identifiers — renaming these makes the product address a
    # model that does not exist
    "openrouter/hermes3:70b",
    "Nous-Hermes-2",
    "Hermes 3",
    # upstream attribution obligations
    "dev@nousresearch.com",
    "[Hermes Agent](https://github.com/NousResearch/hermes-agent)",
]


def _rewrite(text: str) -> str:
    return rename_plobi.rewrite_text(text, ["hermes", "vaelis"])[0]


@pytest.mark.parametrize(("src", "dst"), REWRITE)
def test_word_shapes_rewrite(src: str, dst: str) -> None:
    assert _rewrite(src) == dst


@pytest.mark.parametrize("text", PRESERVE)
def test_foreign_identifiers_survive(text: str) -> None:
    assert _rewrite(text) == text


def test_codemod_is_idempotent() -> None:
    """A second pass over already-renamed text must find nothing to do."""
    once, first = rename_plobi.rewrite_text(
        "\n".join([s for s, _ in REWRITE] + PRESERVE), ["hermes", "vaelis"]
    )
    twice, second = rename_plobi.rewrite_text(once, ["hermes", "vaelis"])
    assert sum(first.values()) > 0
    assert sum(second.values()) == 0
    assert once == twice


def test_path_rename_plan_follows_the_same_rules() -> None:
    assert rename_plobi.plan_renames(
        ["vaelis/agenda/store.py", "hermes_cli/main.py", "utils.py"],
        ["vaelis", "hermes"],
    ) == [
        ("vaelis/agenda/store.py", "plobi/agenda/store.py"),
        ("hermes_cli/main.py", "plobi_cli/main.py"),
    ]
