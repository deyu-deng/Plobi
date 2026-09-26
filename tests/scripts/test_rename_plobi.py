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
    # Relative module paths and glued service names must go through the generic
    # rule, not the ./hermes launcher override -- an unanchored override turned
    # these into '../plobi.py' and 'plobi.py-gateway.service'.
    ("from '../hermes'", "from '../plobi'"),
    ("/user.slice/.../hermes-gateway.service", "/user.slice/.../plobi-gateway.service"),
    ("callPackage ./hermes-agent.nix", "callPackage ./plobi-agent.nix"),
    ("C:/Users/.../hermes-snap-1.sh", "C:/Users/.../plobi-snap-1.sh"),
    # A label naming us glued to an upstream URL: the label is ours (rewrite),
    # the URL is theirs (survive).
    ("[Vaelis Agent](https://github.com/NousResearch/hermes-agent)",
     "[Plobi Agent](https://github.com/NousResearch/hermes-agent)"),
    # URLs that point at things we own are exactly the URLs the codemod SHOULD
    # rewrite -- the placeholder hosts docs/tests use, and our own repo.
    ("https://github.com/deyu-deng/Vaelis/issues",
     "https://github.com/deyu-deng/Plobi/issues"),
    ("https://raw.githubusercontent.com/deyu-deng/Vaelis/main/install.sh",
     "https://raw.githubusercontent.com/deyu-deng/Plobi/main/install.sh"),
    ("https://hermes.example/auth/callback", "https://plobi.example/auth/callback"),
    ("https://github.com/example/hermes-agent.git",
     "https://github.com/example/plobi-agent.git"),
    ("https://img.shields.io/badge/Docs-Hermes-FFD700",
     "https://img.shields.io/badge/Docs-Plobi-FFD700"),
    # A lowercase quoted stem is an identifier in argv/config, not a mention of
    # the word -- it must still rename.
    ("['hermes', 'gateway']", "['plobi', 'gateway']"),
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
    # ── External resources. S3 rewrote 147 URLs + 15 issue tokens into 404s and
    # an unpullable image; these rows are the classes that broke, kept verbatim
    # so a re-run cannot re-break them.
    "https://hermes-agent.nousresearch.com/install.sh",
    "https://hermes-agent.nousresearch.com/docs/user-guide/features/kanban",
    "https://setup.hermes-agent.nousresearch.com",
    "docker pull ghcr.io/nousresearch/hermes-agent:latest",
    "image: nousresearch/hermes-agent:latest",
    "See hermes-agent#21444 for symptom history.",
    "Surface 8 of NousResearch/hermes-agent#47072",
    "https://docs.honcho.dev/v3/guides/integrations/hermes",
    "https://github.com/teknium1/nous-discord-archive/blob/main/archives/hermes-agent.txt",
    "https://medium.com/@jsong_49820/how-i-built-a-self-improving-llm-wiki-with-hermes-agent",
    "https://www.reddit.com/r/hermesagent/comments/1snfnq9/yes_hermes_and_qwen354b_is_all_i_need_details/",
    "https://hermes.fly.dev/auth/callback",
    "https://hermes-roy.tail.ts.net",
    # Mentioning the name as a word (attribution prose) and naming the upstream
    # build -- both denote the real Hermes, never our product.
    '"Hermes" and "Nous Research" are the names of that upstream project',
    "Was 'nous' (Nous Portal) in the upstream Hermes build.",
    # The first pass missed the capitalised namespace and the SSH remote form,
    # which broke scripts/install.sh's clone URL and plobi_cli/main.py's fork
    # detection. Both shapes must survive a re-run.
    'REPO_URL_SSH="git@github.com:NousResearch/hermes-agent.git"',
    "NousResearch/Hermes-3-Llama-3.1-405B",
    "NousResearch/terminal-tasks-glm-hermes-agent",
    # Verbatim shapes found by auditing the residue after the first rollback:
    # a scheme-less docs host, "Nous Hermes" prose, and the non-agentic
    # matcher's own pattern + its Ollama tag counterexamples.
    "expect(view.title).toBe('Failed to open hermes-agent.nousresearch.com/docs')",
    "# Warn if the configured model is a Nous Hermes LLM (not agentic)",
    'r"(?:^|[/:])hermes[-_ ]?[34](?:[-_.:]|$)"',
    "hermes-brain:qwen3-14b-ctx16k",
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


def test_root_launcher_does_not_collide_with_the_package_dir() -> None:
    """`hermes` (repo-root launcher) becomes plobi.py, not plobi/.

    S2 turns the `vaelis/` package into `plobi/`, so a stem-for-stem rename of
    the root launcher would aim at an occupied name. Both the content reference
    and the path plan must agree on plobi.py.
    """
    assert _rewrite("run ./hermes from the checkout") == "run ./plobi.py from the checkout"
    assert rename_plobi.plan_renames(["hermes", "hermes_cli/main.py"], ["hermes"]) == [
        ("hermes", "plobi.py"),
        ("hermes_cli/main.py", "plobi_cli/main.py"),
    ]
