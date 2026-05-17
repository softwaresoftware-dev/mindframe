"""Hermetic integrity tests for the demo knowledge vault.

The incident-triage skills grep `launch/stage/vault/` for runbooks and
service ownership. If the vault fixture drifts — a broken foreign key, a
missing frontmatter field, a grep that stops matching — triage silently
degrades. These tests pin the vault contract documented in
`launch/stage/vault/README.md`.

The vault is deliberately sparse: services and incidents may reference
runbooks that were never written (only the OOMKilled demo path has a
runbook). So foreign keys are checked only in the directions the triage
demo actually depends on — never "service lists a runbook that exists".
"""

import os

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VAULT = os.path.join(ROOT, "launch", "stage", "vault")


def _frontmatter(path):
    """Parse the leading --- YAML block of a markdown note. {} if absent."""
    with open(path) as f:
        text = f.read()
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    return yaml.safe_load(text[3:end]) or {}


def _notes(subdir):
    """[(stem, abspath)] for every .md note in a vault subdirectory."""
    d = os.path.join(VAULT, subdir)
    if not os.path.isdir(d):
        return []
    return [
        (os.path.splitext(f)[0], os.path.join(d, f))
        for f in sorted(os.listdir(d))
        if f.endswith(".md")
    ]


def _grep(pattern):
    """Relative paths of vault files containing `pattern` (substring)."""
    hits = []
    for sub in ("services", "runbooks", "incidents"):
        for _stem, path in _notes(sub):
            with open(path) as f:
                if pattern in f.read():
                    hits.append(os.path.relpath(path, VAULT))
    return set(hits)


pytestmark = pytest.mark.skipif(
    not os.path.isdir(VAULT), reason=f"demo vault not present at {VAULT}"
)


def test_vault_has_the_expected_buckets():
    for sub in ("services", "runbooks", "incidents"):
        assert os.path.isdir(os.path.join(VAULT, sub)), f"missing {sub}/"


def test_every_note_has_parseable_frontmatter():
    for sub in ("services", "runbooks", "incidents"):
        for stem, path in _notes(sub):
            fm = _frontmatter(path)
            assert isinstance(fm, dict) and fm, f"{sub}/{stem}.md: no frontmatter"


def test_note_id_matches_filename():
    """The frontmatter id key equals the filename stem — grep keys off it."""
    id_key = {"services": "service", "runbooks": "runbook", "incidents": "incident"}
    for sub, key in id_key.items():
        for stem, path in _notes(sub):
            fm = _frontmatter(path)
            assert fm.get(key) == stem, (
                f"{sub}/{stem}.md: frontmatter {key}={fm.get(key)!r} != filename"
            )


def test_service_notes_have_required_fields():
    for stem, path in _notes("services"):
        fm = _frontmatter(path)
        for field in ("service", "owner_team", "repo"):
            assert fm.get(field), f"services/{stem}.md missing '{field}'"


def test_runbook_service_foreign_key_resolves():
    """Every runbook points at a service note that exists."""
    services = {stem for stem, _ in _notes("services")}
    for stem, path in _notes("runbooks"):
        fm = _frontmatter(path)
        svc = fm.get("service")
        assert svc in services, (
            f"runbooks/{stem}.md: service '{svc}' has no services/ note"
        )
        assert fm.get("failure_modes"), f"runbooks/{stem}.md missing failure_modes"


def test_incident_service_foreign_keys_resolve():
    """Every incident's services_affected all resolve to service notes."""
    services = {stem for stem, _ in _notes("services")}
    for stem, path in _notes("incidents"):
        fm = _frontmatter(path)
        affected = fm.get("services_affected") or []
        assert affected, f"incidents/{stem}.md has empty services_affected"
        for svc in affected:
            assert svc in services, (
                f"incidents/{stem}.md: services_affected '{svc}' has no note"
            )


def test_grep_contract_oomkilled():
    """`grep OOMKilled` returns the OOM runbook, the March incident, and the
    payments service note (per the k8s-triage README's grep contract).

    The exactness matters in one direction: the April incidents must NOT
    match — a false positive there would mislead k8s-triage onto the wrong
    runbook.
    """
    hits = _grep("OOMKilled")
    assert hits == {
        "runbooks/payments-api-OOM.md",
        "incidents/I-2026-03-14-payments-OOM.md",
        "services/payments-api.md",
    }, hits
    assert not any("2026-04" in h for h in hits), f"April incident matched: {hits}"


def test_grep_contract_oncall_channel():
    """`grep #oncall-payments` reaches the payments service note + its runbook."""
    hits = _grep("#oncall-payments")
    assert "services/payments-api.md" in hits, hits
    assert "runbooks/payments-api-OOM.md" in hits, hits


def test_grep_contract_service_key():
    """`grep 'service: payments-api'` reaches the service note + its runbook."""
    hits = _grep("service: payments-api")
    assert "services/payments-api.md" in hits, hits
    assert "runbooks/payments-api-OOM.md" in hits, hits
