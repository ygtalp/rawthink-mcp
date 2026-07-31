"""One-off migration to the controlled schema.

Moves an existing memory.jsonl from free-text `entityType` and free-text
`relationType` onto the controlled vocabulary in config.py.

Three things happen:

1. `entityType` is mapped onto the ten roles. The old values conflated two
   different questions — what role does this node play, and what subject area
   is it in — which is why the list grew to one type per subject. `domain` is
   split out, and inferred only where the old type actually implied it.

2. `relationType` is folded onto the canonical vocabulary. Nothing is dropped:
   the original string is kept in `original_type` so the mapping is reversible
   and reviewable.

3. `epistemic` and `visibility` are backfilled. `epistemic` becomes `unknown`,
   not `assertion` — an unstated claim must not be promoted to a stated one by
   a migration.

Dry run is the default. Nothing is written unless --apply is passed, and
--apply refuses to run without a backup.

    python -m rawthink_mcp.migrate                 # report only
    python -m rawthink_mcp.migrate --apply         # backup, then write
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import config

_TODAY = datetime.now().date().isoformat()

# ---------------------------------------------------------------------------
# entityType -> (role, inferred domain or None)
#
# Domain is only filled in where the old type genuinely carried it. For the
# rest it is left absent: the migration does not know, and guessing would put
# wrong data into a field whose whole purpose is filtering.
# ---------------------------------------------------------------------------
TYPE_MAP: dict[str, tuple[str, str | None]] = {
    # decisions
    "karar": ("decision", None),
    "decision": ("decision", None),
    "teknik-karar": ("decision", "software"),
    # concepts
    "kavram": ("concept", None),
    "teori": ("concept", None),
    "analoji": ("concept", None),
    "mimari-fikir": ("concept", "software"),
    "mimari": ("concept", "software"),
    "norobiyolojik-mekanizma": ("concept", "neuro"),
    "fenomenoloji": ("concept", "philosophy"),
    # findings
    "bug": ("finding", "software"),
    "bug-fix": ("finding", "software"),
    "teknik-bulgu": ("finding", "software"),
    "teknik-sorun": ("finding", "software"),
    "sorun": ("finding", None),
    "analiz": ("finding", None),
    "arastirma": ("finding", None),
    "test-sonucu": ("finding", "software"),
    "audit": ("finding", "software"),
    "saglik-bulgusu": ("finding", "health"),
    "saglik-verisi": ("finding", "health"),
    # rules and patterns
    "kural": ("rule", None),
    "rule": ("rule", None),
    "pattern": ("rule", "software"),
    "bug-pattern": ("rule", "software"),
    # open questions
    "acik-soru": ("open-question", None),
    # artifacts
    "proje": ("artifact", None),
    "project": ("artifact", None),
    "kaynak": ("artifact", None),
    "icerik": ("artifact", None),
    "feature": ("artifact", "software"),
    "tooling": ("artifact", "software"),
    "system": ("artifact", "software"),
    "plan": ("artifact", None),
    # insights and knowledge
    "icgoru": ("insight", None),
    "insight": ("insight", None),
    "teknik-bilgi": ("insight", "software"),
    "pratik-bilgi": ("insight", None),
    "bilgi": ("insight", None),
    # tasks
    "gorev": ("task", None),
    # events
    "olay": ("event", None),
    "session": ("event", None),
    "milestone": ("event", None),
    "proje-durumu": ("event", None),
    # things
    "kisi": ("thing", None),
    "molekul": ("thing", "health"),
}

# ---------------------------------------------------------------------------
# relationType -> canonical
#
# config.RELATION_ALIASES covers the common synonyms; this extends it with the
# long tail found in a real vault. The one-off descriptive types collapse to
# `related_to` — the specific wording is not lost, it moves to `original_type`.
# ---------------------------------------------------------------------------
EXTRA_ALIASES: dict[str, str] = {
    "identified": "investigates",
    "discovered": "investigates",
    "discovered_alongside": "investigates",
    "discovered_during": "investigates",
    "discovered-during-project": "investigates",
    "implemented": "exemplifies",
    "implements": "exemplifies",
    "instantiates": "exemplifies",
    "instantiated_by": "exemplifies",
    "manifestation_of": "exemplifies",
    "motivates": "caused_by",
    "driven_by": "caused_by",
    "simplifies": "enables",
    "distributes": "enables",
    "arttirir": "enables",
    "has_plan": "part_of",
    "sahip": "part_of",
    "configured_for": "depends_on",
    "reframed_by": "evolved_into",
    "evolves": "evolved_into",
    "complements": "supports",
    "deepened_by": "supports",
    "teorik-cerceve-sunar": "supports",
    "analogous_to": "related_to",
    "contrasts_with": "related_to",
    "candidate_for": "related_to",
    "alternative_to": "related_to",
    "paralel-hipotez": "related_to",
    "fenomenolojik-karsilik-uretir": "related_to",
    "dogrudan-fenomenolojik-karsilik": "related_to",
    "interface-cozulmesi-olarak-yorumlanabilir": "related_to",
    "baska-katman-processleri-olabilir": "related_to",
    "kulturel-baglamini-olusturur": "related_to",
    "beyin-entropisini-artirir": "related_to",
    "immun-disregulasyon-ile-baglantili": "related_to",
}


# ---------------------------------------------------------------------------
# Optional domain guessing
#
# Opt-in, never automatic. Type-inferred domains are deterministic — the old
# type genuinely carried the subject area. These are guesses from names and
# observation text, and a wrong guess in a filter field is worse than an empty
# one, so they are only written when explicitly asked for and always reported
# per entity so they can be reviewed.
# ---------------------------------------------------------------------------
NAME_PREFIXES: dict[str, str] = {
    "aidevtycoon": "software", "banking": "software", "rawthink": "software",
    "pexec": "software", "unity": "software", "claude": "software",
    "mcp": "software", "qdrant": "software",
}

KEYWORDS: dict[str, list[str]] = {
    "software": ["kod", "code", "api", "repo", "commit", "test", "bug", "deploy",
                 "python", "java", "unity", "mcp", "qdrant", "embedding",
                 "session", "plugin", "git", "sql", "docker", "refactor"],
    "philosophy": ["bilinc", "ozgur", "irade", "varolus", "ontoloj",
                   "epistemolo", "metafizik", "simulasyon"],
    "health": ["saglik", "uyku", "beslenme", "tahlil", "vitamin"],
    "neuro": ["noro", "beyin", "dopamin", "serotonin", "sinaps"],
    "music": ["muzik", "gitar", "akor", "nota", "ritim", "album"],
    "writing": ["makale", "yazi", "blog", "dev.to", "taslak"],
    "finance": ["finans", "yatirim", "borsa", "faiz", "portfoy"],
}


def guess_domain(entity: dict) -> tuple[str | None, str | None]:
    """Return (domain, how) or (None, None). A prefix match is strong; a
    keyword match needs at least two hits before it is worth anything."""
    name = entity.get("name", "").lower()
    for prefix, dom in NAME_PREFIXES.items():
        if name.startswith(prefix) or f"-{prefix}-" in name:
            return dom, "prefix"
    text = name + " " + " ".join(
        o.get("text", "") if isinstance(o, dict) else str(o)
        for o in entity.get("observations", [])
    ).lower()
    best, best_score = None, 0
    for dom, words in KEYWORDS.items():
        score = sum(1 for w in words if w in text)
        if score > best_score:
            best, best_score = dom, score
    return (best, "keyword") if best_score >= 2 else (None, None)


def _all_aliases() -> dict[str, str]:
    merged = dict(config.RELATION_ALIASES)
    merged.update(EXTRA_ALIASES)
    return merged


def _norm_key(value: str) -> str:
    return str(value).strip().lower().replace(" ", "_").replace("-", "_")


# ---------------------------------------------------------------------------
# Dangling edge repair
#
# Relations were never checked against the entity list, so the graph carries
# edges pointing at names that do not exist. They are invisible in normal use —
# the edge is simply never traversable — which is the failure mode worth
# fixing: nothing errors, the connection just quietly is not there.
#
# Two repairs, chosen per case:
#
#   repoint — the dangling name is an exact prefix of exactly one entity. That
#             is a rename whose old references were left behind, and pointing
#             them at the new name recovers the original meaning.
#
#   stub    — no such match. A minimal entity is created, marked as
#             reconstructed and epistemic=unknown. Dropping the edge instead
#             would discard the one thing we do know: something pointed here.
# ---------------------------------------------------------------------------

def _stub_type(name: str) -> str:
    low = name.lower()
    if low.startswith("project:") or low.startswith("project/"):
        return "artifact"
    if low.endswith("-pattern") or low.endswith("-kurali"):
        return "rule"
    return "concept"


def heal_dangling(items: list[dict], guess_domains: bool = False) -> dict:
    """Repair edges pointing at unknown entities. Mutates `items` in place."""
    entities = {i["name"]: i for i in items if i.get("type") == "entity"}
    relations = [i for i in items if i.get("type") == "relation"]
    names = set(entities)

    dangling: dict[str, list[dict]] = {}
    for rel in relations:
        for side in ("from", "to"):
            if rel[side] not in names:
                dangling.setdefault(rel[side], []).append(rel)

    report = {"repointed": [], "stubbed": [], "edges": 0}

    for missing, edges in dangling.items():
        report["edges"] += len(edges)
        prefix_matches = [n for n in names if n.startswith(missing) and n != missing]

        if len(prefix_matches) == 1:
            target = prefix_matches[0]
            for rel in edges:
                for side in ("from", "to"):
                    if rel[side] == missing:
                        rel[side] = target
                        rel.setdefault("repointed_from", missing)
            report["repointed"].append((missing, target, len(edges)))
            continue

        stub = {
            "type": "entity",
            "name": missing,
            "entityType": _stub_type(missing),
            "epistemic": config.DEFAULT_EPISTEMIC,
            "visibility": config.DEFAULT_VISIBILITY,
            "observations": [{
                "text": ("Reconstructed from a dangling reference during schema "
                         "migration. Nothing was recorded about it directly; it is "
                         "here so the edges pointing at it stay traversable."),
                "kind": "note", "status": "active",
            }],
            "activation": 0.0,
            "last_accessed": _TODAY,
            "reconstructed": True,
        }
        if guess_domains:
            dom, _how = guess_domain(stub)
            if dom:
                stub["domain"] = dom
        items.append(stub)
        names.add(missing)
        report["stubbed"].append((missing, stub["entityType"],
                                  stub.get("domain"), len(edges)))

    return report


def migrate_items(items: list[dict], guess_domains: bool = False,
                  heal: bool = False) -> tuple[list[dict], dict]:
    """Return migrated items and a report. Pure — does not touch disk."""
    aliases = _all_aliases()
    # aliases are declared with both '-' and '_' spellings in places; index both
    alias_index = {_norm_key(k): v for k, v in aliases.items()}
    type_index = {_norm_key(k): v for k, v in TYPE_MAP.items()}

    rep = {
        "entities": 0, "relations": 0,
        "type_changed": Counter(), "type_unmapped": Counter(),
        "domain_inferred": Counter(), "domain_missing": [],
        "rel_changed": Counter(), "rel_unmapped": Counter(),
        "epistemic_backfilled": 0, "visibility_backfilled": 0,
        "obs_kind_backfilled": 0, "obs_created_missing": 0,
        "domain_guessed": Counter(), "guesses": [],
    }

    out = []
    for item in items:
        kind = item.get("type")

        if kind == "entity":
            rep["entities"] += 1
            old_type = item.get("entityType", "")
            mapped = type_index.get(_norm_key(old_type))
            if mapped:
                new_type, inferred_domain = mapped
                if new_type != old_type:
                    rep["type_changed"][f"{old_type} -> {new_type}"] += 1
                item["entityType"] = new_type
                if inferred_domain and "domain" not in item:
                    item["domain"] = inferred_domain
                    rep["domain_inferred"][inferred_domain] += 1
            elif old_type in config.ENTITY_TYPES:
                pass  # already canonical
            else:
                rep["type_unmapped"][old_type] += 1

            if "domain" not in item and guess_domains:
                dom, how = guess_domain(item)
                if dom:
                    item["domain"] = dom
                    rep["domain_guessed"][dom] += 1
                    rep["guesses"].append((item.get("name", "?"), dom, how))
            if "domain" not in item:
                rep["domain_missing"].append(item.get("name", "?"))

            if "epistemic" not in item:
                item["epistemic"] = config.DEFAULT_EPISTEMIC
                rep["epistemic_backfilled"] += 1
            if "visibility" not in item:
                item["visibility"] = config.DEFAULT_VISIBILITY
                rep["visibility_backfilled"] += 1

            for obs in item.get("observations", []):
                if isinstance(obs, dict):
                    if "kind" not in obs:
                        obs["kind"] = "note"
                        rep["obs_kind_backfilled"] += 1
                    if "created" not in obs:
                        rep["obs_created_missing"] += 1

        elif kind == "relation":
            rep["relations"] += 1
            old_rel = item.get("relationType", "")
            key = _norm_key(old_rel)
            new_rel = None
            if old_rel in config.RELATION_TYPES:
                new_rel = old_rel
            elif key in alias_index:
                new_rel = alias_index[key]
            elif key in config.RELATION_TYPES:
                new_rel = key

            if new_rel is None:
                rep["rel_unmapped"][old_rel] += 1
            elif new_rel != old_rel:
                rep["rel_changed"][f"{old_rel} -> {new_rel}"] += 1
                item["original_type"] = old_rel
                item["relationType"] = new_rel

        out.append(item)

    if heal:
        rep["heal"] = heal_dangling(out, guess_domains=guess_domains)
    else:
        # Report the problem even when not fixing it, so it is visible.
        names = {i["name"] for i in out if i.get("type") == "entity"}
        rep["dangling_found"] = sorted(
            {n for i in out if i.get("type") == "relation"
             for n in (i["from"], i["to"]) if n not in names}
        )

    return out, rep


def _read(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def _write(path: Path, items: list[dict]) -> None:
    text = "\n".join(json.dumps(i, ensure_ascii=False) for i in items)
    tmp = path.with_suffix(".jsonl.migrating")
    tmp.write_text(text + "\n", encoding="utf-8")
    tmp.replace(path)


def print_report(rep: dict, applied: bool) -> None:
    head = "APPLIED" if applied else "DRY RUN — nothing written"
    print(f"\n=== rawthink schema migration — {head} ===\n")
    print(f"  entities   : {rep['entities']}")
    print(f"  relations  : {rep['relations']}")

    print(f"\n  entityType remapped ({sum(rep['type_changed'].values())} entities):")
    for k, v in rep["type_changed"].most_common():
        print(f"    {v:>3}  {k}")
    if rep["type_unmapped"]:
        print("\n  UNMAPPED entityType (left as-is, will be rejected on next write):")
        for k, v in rep["type_unmapped"].most_common():
            print(f"    {v:>3}  {k}")

    print(f"\n  domain inferred ({sum(rep['domain_inferred'].values())} entities):")
    for k, v in rep["domain_inferred"].most_common():
        print(f"    {v:>3}  {k}")
    if rep.get("domain_guessed"):
        print(f"\n  domain GUESSED ({sum(rep['domain_guessed'].values())} entities — review these):")
        for k, v in rep["domain_guessed"].most_common():
            print(f"    {v:>3}  {k}")

    print(f"\n  domain still MISSING: {len(rep['domain_missing'])} entities")
    print("    The old type did not carry a subject area, so the migration does")
    print("    not invent one. These stay writable; they just will not appear in")
    print("    domain-filtered searches until classified.")

    print(f"\n  relationType folded ({sum(rep['rel_changed'].values())} relations,"
          f" original kept in `original_type`):")
    for k, v in rep["rel_changed"].most_common(12):
        print(f"    {v:>3}  {k}")
    if len(rep["rel_changed"]) > 12:
        print(f"    ... and {len(rep['rel_changed']) - 12} more mappings")
    if rep["rel_unmapped"]:
        print("\n  UNMAPPED relationType:")
        for k, v in rep["rel_unmapped"].most_common():
            print(f"    {v:>3}  {k}")

    heal = rep.get("heal")
    if heal:
        print(f"\n  dangling edges repaired ({heal['edges']} edges):")
        for old, new, n in heal["repointed"]:
            print(f"    repointed  {n}x  '{old}'  ->  '{new}'  (rename)")
        for name, etype, dom, n in heal["stubbed"]:
            print(f"    stub       {n}x  '{name}'  as {etype}"
                  f"{'/' + dom if dom else ''}  (marked reconstructed)")
    elif rep.get("dangling_found"):
        print(f"\n  DANGLING edges found ({len(rep['dangling_found'])} unknown targets):")
        for n in rep["dangling_found"]:
            print(f"    {n}")
        print("    These edges are not traversable. Re-run with --heal-dangling to repair.")

    print(f"\n  backfilled : epistemic={rep['epistemic_backfilled']}"
          f"  visibility={rep['visibility_backfilled']}"
          f"  observation kind={rep['obs_kind_backfilled']}")
    print(f"  observations without a `created` date: {rep['obs_created_missing']}"
          " (left alone — inventing a date would be worse than none)")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Migrate memory.jsonl to the controlled schema.")
    ap.add_argument("--path", default=config.MEMORY_FILE, help="memory.jsonl path")
    ap.add_argument("--apply", action="store_true", help="write changes (default: dry run)")
    ap.add_argument("--guess-domains", action="store_true",
                    help="also infer domain from names and observation text (a guess — review the report)")
    ap.add_argument("--heal-dangling", action="store_true",
                    help="repair relations pointing at unknown entities (repoint renames, stub the rest)")
    ap.add_argument("--list-missing-domain", action="store_true",
                    help="print the entities that still need a domain")
    args = ap.parse_args(argv)

    path = Path(args.path)
    if not path.exists():
        print(f"not found: {path}", file=sys.stderr)
        return 1

    items = _read(path)
    migrated, rep = migrate_items(items, guess_domains=args.guess_domains, heal=args.heal_dangling)
    print_report(rep, applied=args.apply)

    if rep.get("guesses"):
        print("\n  guessed domains, per entity:")
        for name, dom, how in rep["guesses"]:
            print(f"    {dom:<11} ({how:<7}) {name}")

    if args.list_missing_domain:
        print("\n  entities without a domain:")
        for n in rep["domain_missing"]:
            print(f"    {n}")

    if not args.apply:
        print("\n  Re-run with --apply to write. A timestamped backup is taken first.")
        return 0

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = path.with_suffix(f".jsonl.bak-{stamp}")
    shutil.copy2(path, backup)
    _write(path, migrated)
    print(f"\n  backup : {backup}")
    print(f"  written: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
