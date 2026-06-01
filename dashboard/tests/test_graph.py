"""Unit tests for the vault graph builder (dashboard/server/server.py).

The graph endpoint is the read side of the knowledge-graph: it turns a vault
of markdown notes into nodes + edges. Edges come from two sources — body
[[wikilinks]] AND frontmatter foreign_keys (per the vault's schema.yaml).
The FK path is what keeps the graph connected; these tests pin its behaviour:
null FKs don't draw edges, unresolvable FKs don't crash, and an edge asserted
both ways is not double-counted.

Loaded via importlib under a unique module name so the basename `server.py`
doesn't collide with mcp/server.py under pytest's prepend import mode. The
module's uvicorn.run is __main__-guarded, so importing starts nothing.
"""
import importlib.util
import pathlib
import textwrap

SERVER_PY = pathlib.Path(__file__).resolve().parents[1] / "server" / "server.py"
_spec = importlib.util.spec_from_file_location("mf_dashboard_server", SERVER_PY)
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)
build_vault_graph = _mod.build_vault_graph


SCHEMA = textwrap.dedent("""\
    schema_version: 2
    entities:
      person:
        directory: People
        identity: name
        foreign_keys: { manager: person }
      repository:
        directory: Repositories
        identity: name
        foreign_keys: { service: service, owner: person }
      service:
        directory: Services
        identity: name
        foreign_keys: { repository: repository, owner: person }
      glossary:
        directory: Glossary
        identity: name
        foreign_keys: { owner: person }
""")


def _note(fm: str, body: str = "") -> str:
    return f"---\n{textwrap.dedent(fm)}---\n{body}"


def _build_vault(root: pathlib.Path) -> None:
    (root / "schema.yaml").write_text(SCHEMA)
    for d in ("People", "Repositories", "Services", "Glossary"):
        (root / d).mkdir()

    # the hub person; manager is null (~) -> must NOT draw an edge
    (root / "People" / "thatcher.md").write_text(_note("""\
        type: person
        name: thatcher
        display_name: Thatcher Thornberry
        manager: ~
    """, "Founder.\n"))

    # repo owned by the person, service null -> exactly one FK edge (-> person)
    (root / "Repositories" / "app.md").write_text(_note("""\
        type: repository
        name: app
        owner: thatcher
        service: ~
    """, "Some repo.\n"))

    # repo that asserts owner BOTH as an FK and as a body wikilink (dedup),
    # plus a real service FK
    (root / "Repositories" / "api.md").write_text(_note("""\
        type: repository
        name: api
        owner: thatcher
        service: web
    """, "Maintained by [[thatcher]].\n"))

    # a service owned by the person
    (root / "Services" / "web.md").write_text(_note("""\
        type: service
        name: web
        owner: thatcher
    """, "Web service.\n"))

    # glossary term whose owner does NOT exist (unresolvable FK -> no edge),
    # but a valid body wikilink to the app repo
    (root / "Glossary" / "term.md").write_text(_note("""\
        type: glossary
        name: term
        owner: ghost-person
    """, "See [[app]].\n"))


def _edge_set(g):
    return {(e["source"], e["target"]) for e in g["edges"]}


def test_fk_and_wikilink_graph(tmp_path):
    _build_vault(tmp_path)
    g = build_vault_graph(tmp_path, "vault")
    edges = _edge_set(g)

    assert g["node_count"] == 5

    # FK edge from frontmatter owner: every owned entity -> the person hub
    assert ("Repositories/app", "People/thatcher") in edges
    assert ("Services/web", "People/thatcher") in edges
    # FK edge between two non-person entities
    assert ("Repositories/api", "Services/web") in edges
    # body [[wikilink]] still produces an edge
    assert ("Glossary/term", "Repositories/app") in edges

    # exactly these five distinct edges — no doubles, no phantom edges
    assert edges == {
        ("Repositories/app", "People/thatcher"),
        ("Repositories/api", "People/thatcher"),
        ("Repositories/api", "Services/web"),
        ("Services/web", "People/thatcher"),
        ("Glossary/term", "Repositories/app"),
    }
    assert g["edge_count"] == 5


def test_null_fk_draws_no_edge(tmp_path):
    _build_vault(tmp_path)
    g = build_vault_graph(tmp_path, "vault")
    edges = _edge_set(g)
    # person.manager: ~ and repository.service: ~ are null -> no edge from them
    assert not any(s == "People/thatcher" for s, _ in edges)          # manager ~
    assert ("Repositories/app", "Services/web") not in edges          # service ~


def test_unresolvable_fk_is_dropped_not_crashing(tmp_path):
    _build_vault(tmp_path)
    g = build_vault_graph(tmp_path, "vault")
    edges = _edge_set(g)
    # glossary owner 'ghost-person' has no node -> no edge, but the node stands
    assert not any(t.endswith("ghost-person") for _, t in edges)
    assert any(n["id"] == "Glossary/term" for n in g["nodes"])


def test_fk_edge_tagged_kind_and_field(tmp_path):
    _build_vault(tmp_path)
    g = build_vault_graph(tmp_path, "vault")
    # app->thatcher comes only from the owner FK (no body wikilink), so it
    # carries the fk tag; this is what distinguishes structural edges
    owner_edge = next(e for e in g["edges"]
                      if e["source"] == "Repositories/app" and e["target"] == "People/thatcher")
    assert owner_edge.get("kind") == "fk"
    assert owner_edge.get("field") == "owner"


def test_display_name_used_as_label(tmp_path):
    _build_vault(tmp_path)
    g = build_vault_graph(tmp_path, "vault")
    person = next(n for n in g["nodes"] if n["id"] == "People/thatcher")
    assert person["label"] == "Thatcher Thornberry"


def test_no_schema_falls_back_to_wikilinks_only(tmp_path):
    # without schema.yaml there are no FK definitions; only body wikilinks
    # should draw edges, and frontmatter FKs are ignored (not crash)
    (tmp_path / "Repositories").mkdir()
    (tmp_path / "People").mkdir()
    (tmp_path / "People" / "thatcher.md").write_text(_note("type: person\nname: thatcher\n"))
    (tmp_path / "Repositories" / "app.md").write_text(
        _note("type: repository\nname: app\nowner: thatcher\n", "See [[thatcher]].\n"))
    g = build_vault_graph(tmp_path, "vault")
    edges = _edge_set(g)
    # body wikilink edge present; the owner FK is NOT drawn (no schema)
    assert ("Repositories/app", "People/thatcher") in edges
    assert g["edge_count"] == 1
