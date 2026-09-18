"""The autonomous harness repairer.

Watches `stuck/` for frozen cases (harness holes, repeated exceptions). For each one it lets a
full Claude Code session (CLI, subscription) diagnose and patch the harness, then *this script*
— not Claude — decides: the case must replay clean, older resolved cases must still pass,
only harness files may have changed. Pass → commit + changelog + hot reload. Fail → revert.

What makes an intervention worth its price:
  - the case is replayed BEFORE Claude is called: a case the current harness already gets out of
    (fixed since by another commit) is closed for free, and the replay trace opens the brief;
  - the brief carries the evidence: Jev's last decisions with their odds, what earlier tries on
    this case changed and why the script rejected them, recent repairs on the same milestone;
  - a rejected patch is kept (essai-N.diff): the next try starts from it instead of from nothing;
  - a CLI outage is not a try;
  - after a merge the LIVE run is watched: did the official game actually get out? (suivi)

    uv run python repair.py            # daemon
    uv run python repair.py --once stuck/0001-trou
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).parent
CASES, LEDGER, CHANGELOG = ROOT / "stuck", ROOT / "repairs.jsonl", ROOT / "changelog.jsonl"
DECISIONS = ROOT / "journal" / "decisions.jsonl"
OUTAGES = 6           # CLI outages tolerated on one case before it counts as a try anyway
ALLOWED = re.compile(r"^(agent|battle|data|game|ram|screen|world|route|supervisor|serve|run|journal)\.py$|^ui\.html$|^README\.md$")
PYTHON = str(ROOT / ".venv/bin/python")

RULES = """Tu es l'ingénieur du harnais d'un agent qui joue à Pokémon Bleu (FR). Lis README.md d'abord.
Principe : une boucle n'est pas un défaut du modèle Jev, c'est un champ manquant ou faux dans l'état, ou une action
manquante dans le menu proposé. Ton travail : trouver lequel, et le corriger avec le plus petit patch possible.

Périmètre STRICT :
- Tu peux modifier : agent.py battle.py data.py game.py ram.py screen.py world.py route.py supervisor.py serve.py ui.html README.md.
- Tu ne modifies JAMAIS : carnet.json, replay.py, repair.py, run_forever.sh, les .state, la ROM, .gitignore.
- Tu améliores la PERCEPTION (lire la RAM, l'écran, la carte) et les ACTIONS offertes. Tu ne décides jamais à la place
  de Jev : pas de choix codé en dur, pas de script de progression, pas de condition « si telle carte alors tel bouton ».
  Un fait lu en RAM et transmis à Jev est légitime ; une règle qui choisit pour lui ne l'est pas.
- Les adresses RAM suivent pret/pokered (anglais) ; la ROM française décale de +5 à partir de 0xCF00 (voir ram.py).
  Toute nouvelle adresse doit être VÉRIFIÉE empiriquement sur le .state du cas (lis-la avant/après, comme find_addrs.py).
- Ne redémarre pas le serveur, ne fais pas de commit : le script appelant s'en charge.

Méthode : 1) lis le dossier fourni dans la demande (la reproduction est DÉJÀ faite, ne la refais pas avant d'avoir patché),
puis case.json, case.png et decisions.jsonl du cas : les cotes de Jev disent entre quoi il hésite et ce qu'il croit ;
2) diagnostique en lisant le code et, si besoin, en sondant la RAM avec un petit script dans /tmp ;
3) patche ; 4) `.venv/bin/python -m py_compile *.py` ; 5) rejoue : `.venv/bin/python replay.py <cas> --decisions 40`
jusqu'à ce qu'il sorte (code retour 0). Chaque replay coûte des décisions Jev réelles : 6 replays maximum.
Si un essai précédent a été rejeté, son patch est dans essai-N.diff : repars-en s'il allait dans le bon sens
(`git apply`), ou dis pourquoi tu l'abandonnes. Ne refais pas ce qui a déjà échoué.

Termine ta réponse par UNE ligne JSON brute :
{"titre": "<4 à 7 mots, comme un titre de chapitre>", "cause": "<ce que le journal montrait, et le champ ou l'action qui manquait — 2 phrases pour un lecteur qui ne connaît pas le code>", "resume": "<ce qui a changé dans le harnais — 1 à 2 phrases>", "titre_en": "<le titre en anglais>", "cause_en": "<la cause en anglais>", "resume_en": "<le résumé en anglais>"}
(la page publique existe en français et en anglais ; noms anglais du jeu : Brock, Pewter City, SCRATCH, EMBER…)"""


def sh(*cmd, timeout=900, **kw):
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=timeout, **kw)


def spent_today() -> float:
    if not LEDGER.exists():
        return 0.0
    today = time.strftime("%Y-%m-%d")
    return sum(e.get("cout_usd", 0) for e in map(json.loads, LEDGER.read_text().splitlines()) if e["date"].startswith(today))


def replay(case: Path, decisions: int = 40) -> dict:
    out = sh(PYTHON, "replay.py", str(case), "--decisions", str(decisions))
    try:
        return json.loads(out.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        return {"sorti": False, "raison": (out.stderr or out.stdout)[-400:]}


def ledger() -> list[dict]:
    return [json.loads(l) for l in LEDGER.read_text().splitlines()] if LEDGER.exists() else []


def live_decisions(after: int = -1) -> list[dict]:
    """The official run's decisions numbered above `after` (the file is append-only, a few MB)."""
    if not DECISIONS.exists():
        return []
    out = []
    for raw in DECISIONS.open(encoding="utf-8"):
        try:
            d = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if d.get("n", 0) > after:
            out.append(d)
    return out


def excerpt(case: Path, meta: dict, count: int = 40) -> None:
    """Jev's last decisions before the freeze, odds included, next to the case."""
    target = case / "decisions.jsonl"
    frozen_at = meta["memoire"]["jev"]["decisions"]
    if target.exists():
        return
    rows = [d for d in live_decisions(frozen_at - count) if d["n"] <= frozen_at]
    with target.open("w", encoding="utf-8") as handle:
        for d in rows:
            handle.write(json.dumps({k: d.get(k) for k in (
                "n", "carte", "position", "source_objectif", "options", "retirees_par_le_harnais", "cotes", "choix",
                "confiance", "effet", "echec", "barre", "apres")} | {"objectif": d["contexte"].get("objectif")},
                ensure_ascii=False) + "\n")


def brief(case: Path, meta: dict, baseline: dict, tries: int) -> str:
    rel = case.relative_to(ROOT)
    parts = [f"Répare le cas {rel} (genre « {meta['genre']} », jalon « {meta.get('jalon')} », carte « {meta.get('carte')} »).",
             f"Lis README.md, puis {rel}/case.json, {rel}/case.png et {rel}/decisions.jsonl "
             "(les dernières décisions de Jev avant le gel : options offertes, options retirées par le harnais, cotes, effet).",
             "\n## Reproduction déjà faite avec le harnais actuel (40 décisions)\n" + json.dumps(baseline, ensure_ascii=False)]
    for n in range(1, tries + 1):
        note = case / f"essai-{n}.json"
        if note.exists():
            past = json.loads(note.read_text())
            parts.append(f"\n## Essai {n} sur ce cas — REJETÉ par le script : {past['motif']}\n"
                         f"Cause annoncée : {past.get('cause')}\nRésumé annoncé : {past.get('resume')}\n"
                         f"Patch conservé : {rel}/essai-{n}.diff")
    same = [e for e in ledger() if e.get("resultat") == "fusionné" and e.get("jalon") == meta.get("jalon")]
    if same:
        parts.append("\n## Réparations déjà fusionnées sur ce même jalon — elles n'ont pas suffi, le jeu officiel s'est rebloqué")
        parts += [f"- {e['cas']} : {e.get('resume')} (suivi en direct : {e.get('suivi', 'pas encore mesuré')})" for e in same[-4:]]
    if CHANGELOG.exists():
        recent = [json.loads(l) for l in CHANGELOG.read_text().splitlines()[-5:]]
        parts.append("\n## Derniers changements du harnais (certains sont postérieurs au gel du cas)")
        parts += [f"- {c.get('date')} {c.get('auteur')} : {c.get('titre')} — {str(c.get('resume'))[:220]}" for c in recent]
    return "\n".join(parts)


def follow_up(window: int) -> None:
    """Did the merge help the official run? A replay passing is a lab result; this is the field one."""
    for case in sorted(CASES.glob("[0-9]*")):
        status = case / "repair.json"
        done = json.loads(status.read_text()) if status.exists() else {}
        if not done.get("resolu") or "fusion_n" not in done or "suivi" in done:
            continue
        meta = json.loads((case / "case.json").read_text())
        if meta["genre"] == "exception":
            continue
        known, since = set(meta["memoire"].get("visited", [])), live_decisions(done["fusion_n"])
        out = next((d for d in since if d.get("jalon") != meta.get("jalon") or d["apres"]["carte"] not in known), None)
        if out:
            verdict = f"sorti en direct {out['n'] - done['fusion_n']} décisions après la fusion ({out['apres']['carte']}, jalon {out.get('jalon')})"
        elif len(since) >= window:
            verdict = f"aucun progrès en direct {len(since)} décisions après la fusion"
        else:
            continue
        done["suivi"] = verdict
        status.write_text(json.dumps(done, ensure_ascii=False))
        lines = ledger()
        for e in lines:
            if e.get("cas") == case.name and e.get("resultat") == "fusionné":
                e["suivi"] = verdict
        LEDGER.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in lines), encoding="utf-8")
        print(json.dumps({"cas": case.name, "suivi": verdict}, ensure_ascii=False), flush=True)


def repair(case: Path, args) -> bool:
    status = case / "repair.json"
    tries = json.loads(status.read_text())["essais"] if status.exists() else 0
    previous = json.loads(status.read_text()) if status.exists() else {}
    meta = json.loads((case / "case.json").read_text(encoding="utf-8"))
    entry = {"date": time.strftime("%Y-%m-%d %H:%M:%S"), "cas": case.name, "essai": tries + 1, "jalon": meta.get("jalon")}
    if sh("git", "status", "--porcelain", "--untracked-files=no").stdout.strip():
        sh("git", "stash")                                   # never build on someone's half-finished edit

    # Before paying for a diagnosis: does the harness as it is today still fail here?
    baseline = replay(case)
    if baseline["sorti"] and replay(case)["sorti"]:
        entry.update(cout_usd=0, duree_s=0, resultat="non reproduit", motif=baseline["raison"])
        status.write_text(json.dumps({**previous, "essais": tries, "resolu": True, "non_reproduit": True}, ensure_ascii=False))
        with LEDGER.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(json.dumps(entry, ensure_ascii=False), flush=True)
        return True
    # An old case is a regression witness only if today's harness, unpatched, still gets out of it: the game mode
    # has changed since some were frozen, and a witness that fails anyway rejects good patches (8 to 10 $ each).
    witnesses = [old.parent for old in sorted(CASES.glob("*/repair.json"))[-args.regressions:]
                 if old.parent != case and json.loads(old.read_text()).get("resolu") and replay(old.parent)["sorti"]]
    try:
        excerpt(case, meta)
    except (OSError, KeyError) as error:
        print(f"extrait du journal impossible : {error!r}", flush=True)

    started, run = time.time(), None
    try:
        run = sh("claude", "-p", "--model", args.model, "--output-format", "json",
                 "--max-budget-usd", str(args.budget), "--append-system-prompt", RULES,
                 "--allowedTools", "Read,Edit,Write,Bash,Grep,Glob", "--permission-mode", "acceptEdits",
                 "--setting-sources", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                 "--disable-slash-commands", "--no-session-persistence",
                 input=brief(case, meta, baseline, tries),
                 timeout=args.timeout)
        reply = json.loads(run.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        reply = {"result": "", "total_cost_usd": 0, "erreur": repr(error)}
    entry.update(cout_usd=reply.get("total_cost_usd", 0), duree_s=round(time.time() - started),
                 tours=reply.get("num_turns"))
    found = re.findall(r"\{[^{}]*\"resume\"[^{}]*\}", reply.get("result", ""))
    verdict = {"titre": case.name, "cause": "?", "resume": "?", **(json.loads(found[-1]) if found else {})}

    changed = [l[3:] for l in sh("git", "status", "--porcelain").stdout.splitlines()]
    if not reply.get("result") and not [f for f in changed if ALLOWED.match(f)] and previous.get("pannes", 0) < OUTAGES:
        # the CLI never worked (quota, network, crash): that is not an attempt at this case
        status.write_text(json.dumps({**previous, "essais": tries, "resolu": False, "pannes": previous.get("pannes", 0) + 1}, ensure_ascii=False))
        entry.update(resultat="panne du réparateur", motif=str(reply.get("erreur") or (run.stderr[-300:] if run else "?")))
        with LEDGER.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
        print(json.dumps(entry, ensure_ascii=False), flush=True)
        time.sleep(300)
        return False
    outside = [f for f in changed if not ALLOWED.match(f) and not f.startswith("stuck/")]
    problem = None
    if outside:
        problem = f"fichiers hors périmètre modifiés : {outside}"
    elif not [f for f in changed if ALLOWED.match(f)]:
        problem = "aucun fichier du harnais modifié"
    elif sh(PYTHON, "-m", "py_compile", *[f for f in changed if f.endswith(".py")]).returncode:
        problem = "le code ne compile pas"
    else:
        result = replay(case)
        if not result["sorti"]:
            problem = f"le cas ne sort toujours pas : {result['raison']}"
        else:
            for old in witnesses:
                if not replay(old)["sorti"]:
                    problem = f"régression sur {old.name}"
                    break

    if problem:
        # keep what was tried: the next attempt must not start blind
        (case / f"essai-{tries + 1}.diff").write_text(sh("git", "diff").stdout, encoding="utf-8")
        (case / f"essai-{tries + 1}.json").write_text(json.dumps({"motif": problem, **verdict, "reponse": reply.get("result", "")[-3000:]}, ensure_ascii=False, indent=1), encoding="utf-8")
        sh("git", "checkout", "--", "."); sh("git", "clean", "-fdq", "--exclude=stuck", "--exclude=*.state")
        entry.update(resultat="rejeté", motif=problem, **verdict)
        status.write_text(json.dumps({"essais": tries + 1, "resolu": False, "motif": problem}, ensure_ascii=False))
    else:
        files = [f for f in changed if ALLOWED.match(f)]
        with CHANGELOG.open("a", encoding="utf-8") as handle:      # part of the same commit: the tree stays clean
            handle.write(json.dumps({"date": entry["date"], "auteur": "réparateur", "cas": case.name, "fichiers": files, **verdict}, ensure_ascii=False) + "\n")
        sh("git", "add", *files, CHANGELOG.name)
        sh("git", "commit", "-q", "-m", f"réparateur : {verdict['resume']}\n\nCas {case.name} — cause : {verdict['cause']}")
        now = live_decisions()
        status.write_text(json.dumps({"essais": tries + 1, "resolu": True, "fusion_n": now[-1]["n"] if now else 0}, ensure_ascii=False))
        # lessons Claude wrote while the harness was wrong are false knowledge: forget them
        lessons, milestone = ROOT / "lecons.json", json.loads((case / "case.json").read_text()).get("jalon")
        if lessons.exists():
            kept = [l for l in json.loads(lessons.read_text()) if l["jalon"] != milestone]
            lessons.write_text(json.dumps(kept, ensure_ascii=False, indent=2))
        entry.update(resultat="fusionné", fichiers=files, **verdict)
        if {"serve.py", "relay.py"} & set(files):               # the server itself cannot hot-swap: restart it
            sh("pkill", "-f", "[p]ython serve.py")
        # everything else is picked up by the running server between two decisions (hot reload)
    with LEDGER.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    print(json.dumps(entry, ensure_ascii=False), flush=True)
    return not problem


def pending(max_tries: int) -> list[Path]:
    out = []
    for case in sorted(CASES.glob("[0-9]*")):
        status = case / "repair.json"
        done = json.loads(status.read_text()) if status.exists() else {"essais": 0, "resolu": False}
        if (case / "case.json").exists() and not done["resolu"] and done["essais"] < max_tries:
            out.append(case)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", help="réparer ce cas puis sortir")
    ap.add_argument("--model", default="opus")
    ap.add_argument("--budget", type=float, default=10.0, help="plafond par intervention, en $ équivalent tarif catalogue (abonnement : rien n'est facturé)")
    ap.add_argument("--daily", type=float, default=100.0, help="garde-fou anti-emballement par jour, même unité")
    ap.add_argument("--tries", type=int, default=3)
    ap.add_argument("--regressions", type=int, default=4, help="anciens cas résolus à rejouer")
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--suivi", type=int, default=150, help="décisions en direct après une fusion avant de conclure qu'elle n'a rien changé")
    args = ap.parse_args()
    if args.once:
        sys.exit(0 if repair(ROOT / args.once, args) else 1)
    while True:
        try:
            follow_up(args.suivi)
        except Exception as error:                            # measuring must never stop repairing
            print(f"suivi impossible : {error!r}", flush=True)
        for case in pending(args.tries):
            if spent_today() >= args.daily:
                break
            repair(case, args)
        time.sleep(30)


if __name__ == "__main__":
    main()
