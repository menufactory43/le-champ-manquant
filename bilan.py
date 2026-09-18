"""Turn the construction log into the harness to-do list.

    uv run python bilan.py [journal/] > bilan.md

Every section answers one question about what Jev still cannot do alone.
"""
from __future__ import annotations
import json, sys
from collections import Counter, defaultdict
from pathlib import Path


def load(root: Path, name: str) -> list[dict]:
    path = root / f"{name}.jsonl"
    return [json.loads(line) for line in path.open(encoding="utf-8")] if path.exists() else []


def kind(action: str) -> str:
    return action.split("_")[0]


def main() -> None:
    root = Path(sys.argv[1] if len(sys.argv) > 1 else "journal")
    decisions, texts = load(root, "decisions"), load(root, "textes")
    claude, marks, spots = load(root, "claude"), load(root, "jalons"), load(root, "reperages")
    print(f"# Bilan de construction — {len(decisions)} décisions de Jev\n")

    print("## 1. Coût par jalon (l'étalon à battre sans aide)\n")
    print("| jalon atteint | décisions cumulées | coût Jev cumulé | appels Claude cumulés | temps joué |\n|---|---|---|---|---|")
    for m in marks:
        calls = (m.get("claude") or {}).get("appels", 0)
        print(f"| {m['de']} → {m['vers']} | {m['decisions']} | {m['cout_jev_usd']:.4f} $ | {calls} | {m['joue_s'] // 60} min |")

    print("\n## 2. Dépendances à Claude — chaque ligne est une aide dont Jev devra se passer\n")
    effects = [c for c in claude if c["moment"] == "effet"]
    open_calls = len([c for c in claude if c["moment"] == "reponse"]) - len(effects)
    for c in effects:
        r = c["reponse"]
        print(f"- jalon `{c['jalon']}`, décision {c['decision']} — progrès {c['decisions_avant_progres']} décisions plus tard ({c['progres']})")
        print(f"  - consigne : {r.get('sous_objectif', '')[:200]}")
        if r.get("lecon"):
            print(f"  - **connaissance injectée** : {r['lecon'][:240]}")
    print(f"\n{len(effects)} interventions suivies d'un progrès, {open_calls} sans progrès à ce jour.")

    print("\n## 3. Décisions sans effet — où Jev gaspille (option mal décrite, ou qui n'aurait pas dû être offerte)\n")
    by_kind, wasted = Counter(kind(d["choix"]) for d in decisions), Counter(kind(d["choix"]) for d in decisions if d["effet"] == ["rien"])
    print("| type d'action | décisions | sans effet | taux |\n|---|---|---|---|")
    for k, n in by_kind.most_common():
        print(f"| {k} | {n} | {wasted[k]} | {100 * wasted[k] / n:.0f} % |")
    print("\nLieux les plus coûteux :")
    for (where, what), n in Counter((d["carte"], d["choix"]) for d in decisions if d["effet"] == ["rien"]).most_common(10):
        print(f"- {where} — `{what}` : {n} fois")

    print("\n## 4. Décisions incertaines (confiance < 0,5) — la mesure dit : une sur deux est fausse\n")
    unsure = [d for d in decisions if d["confiance"] < 0.5]
    print(f"{len(unsure)} sur {len(decisions)} ({100 * len(unsure) / max(1, len(decisions)):.0f} %). Par carte :")
    for where, n in Counter(d["carte"] for d in unsure).most_common(8):
        print(f"- {where} : {n}")
    for d in sorted(unsure, key=lambda d: d["confiance"])[:5]:
        top = sorted(d["cotes"].items(), key=lambda kv: -kv[1])[:3]
        print(f"  - {d['carte']} {d['position']} : {', '.join(f'{k} {v:.0%}' for k, v in top)} → effet {d['effet']}")

    print("\n## 5. Ce que le harnais a décidé à la place de Jev\n")
    withheld = Counter(reason for d in decisions for reason in d["retirees_par_le_harnais"].values())
    print(f"- options retirées du menu : {dict(withheld)}")
    print(f"- décisions prises sous une alerte du harnais : {sum(1 for d in decisions if d.get('alerte_du_harnais'))}")
    print(f"- passages barrés constatés : {sum(1 for d in decisions if d.get('barre'))}")
    print(f"- macros échouées : {sum(1 for d in decisions if d.get('echec'))}")
    sources = Counter(d["source_objectif"] for d in decisions)
    print(f"- qui formulait l'objectif : {dict(sources)}")

    print("\n## 6. Repérage du jalon par Jev\n")
    ids = [s["choix"] for s in spots]
    print(f"{len(spots)} jugements, confiance médiane {sorted(s['confiance'] for s in spots)[len(spots) // 2]:.2f}." if spots else "aucun.")
    for s in spots:
        if s["avant"] and s["choix"] != s["avant"]:
            print(f"- décision {s['decision']} : {s['avant']} → {s['choix']} (confiance {s['confiance']:.2f}, carte {s['faits']['carte']})")

    print("\n## 7. Textes du jeu les plus répétés — un mur ou un indice que Jev n'exploite pas\n")
    pages = Counter(p for t in texts if not t["combat"] for p in t["pages"])
    for page, n in pages.most_common(12):
        if n >= 3:
            print(f"- ×{n} : « {page[:110]} »")


if __name__ == "__main__":
    main()
