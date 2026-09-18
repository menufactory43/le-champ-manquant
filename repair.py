"""The autonomous harness repairer.

Watches `stuck/` for frozen cases (harness holes, repeated exceptions). For each one it lets a
full Claude Code session (CLI, subscription) diagnose and patch the harness, then *this script*
— not Claude — decides: the case must replay clean, older resolved cases must still pass,
only harness files may have changed. Pass → commit + changelog + restart. Fail → revert.

    uv run python repair.py            # daemon
    uv run python repair.py --once stuck/0001-trou
"""
from __future__ import annotations
import argparse, json, os, re, subprocess, sys, time
from pathlib import Path

ROOT = Path(__file__).parent
CASES, LEDGER, CHANGELOG = ROOT / "stuck", ROOT / "repairs.jsonl", ROOT / "changelog.jsonl"
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

Méthode : 1) lis le cas (case.json, case.png) ; 2) reproduis : `.venv/bin/python replay.py <cas> --decisions 25` ;
3) diagnostique en lisant le code et, si besoin, en sondant la RAM avec un petit script dans /tmp ;
4) patche ; 5) `.venv/bin/python -m py_compile *.py` ; 6) rejoue le cas jusqu'à ce qu'il sorte (code retour 0).
Chaque replay coûte des décisions Jev réelles : reste raisonnable (6 replays maximum).

Termine ta réponse par UNE ligne JSON brute :
{"cause": "<le champ ou l'action qui manquait>", "resume": "<une phrase pour le changelog public>"}"""


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


def repair(case: Path, args) -> bool:
    status = case / "repair.json"
    tries = json.loads(status.read_text())["essais"] if status.exists() else 0
    entry = {"date": time.strftime("%Y-%m-%d %H:%M:%S"), "cas": case.name, "essai": tries + 1}
    if sh("git", "status", "--porcelain", "--untracked-files=no").stdout.strip():
        sh("git", "stash")                                   # never build on someone's half-finished edit

    started = time.time()
    try:
        run = sh("claude", "-p", "--model", args.model, "--output-format", "json",
                 "--max-budget-usd", str(args.budget), "--append-system-prompt", RULES,
                 "--allowedTools", "Read,Edit,Write,Bash,Grep,Glob", "--permission-mode", "acceptEdits",
                 "--setting-sources", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                 "--disable-slash-commands", "--no-session-persistence",
                 input=f"Répare le cas {case.relative_to(ROOT)}. Commence par lire README.md puis {case.relative_to(ROOT)}/case.json.",
                 timeout=args.timeout)
        reply = json.loads(run.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as error:
        reply = {"result": "", "total_cost_usd": 0, "erreur": repr(error)}
    entry.update(cout_usd=reply.get("total_cost_usd", 0), duree_s=round(time.time() - started),
                 tours=reply.get("num_turns"))
    found = re.findall(r"\{[^{}]*\"resume\"[^{}]*\}", reply.get("result", ""))
    verdict = json.loads(found[-1]) if found else {"cause": "?", "resume": "?"}

    changed = [l[3:] for l in sh("git", "status", "--porcelain").stdout.splitlines()]
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
            for old in sorted(CASES.glob("*/repair.json"))[-args.regressions:]:
                if old.parent != case and json.loads(old.read_text()).get("resolu") and not replay(old.parent)["sorti"]:
                    problem = f"régression sur {old.parent.name}"
                    break

    if problem:
        sh("git", "checkout", "--", "."); sh("git", "clean", "-fdq", "--exclude=stuck", "--exclude=*.state")
        entry.update(resultat="rejeté", motif=problem, **verdict)
        status.write_text(json.dumps({"essais": tries + 1, "resolu": False, "motif": problem}, ensure_ascii=False))
    else:
        files = [f for f in changed if ALLOWED.match(f)]
        with CHANGELOG.open("a", encoding="utf-8") as handle:      # part of the same commit: the tree stays clean
            handle.write(json.dumps({"date": entry["date"], "cas": case.name, "fichiers": files, **verdict}, ensure_ascii=False) + "\n")
        sh("git", "add", *files, CHANGELOG.name)
        sh("git", "commit", "-q", "-m", f"réparateur : {verdict['resume']}\n\nCas {case.name} — cause : {verdict['cause']}")
        status.write_text(json.dumps({"essais": tries + 1, "resolu": True}, ensure_ascii=False))
        # lessons Claude wrote while the harness was wrong are false knowledge: forget them
        lessons, milestone = ROOT / "lecons.json", json.loads((case / "case.json").read_text()).get("jalon")
        if lessons.exists():
            kept = [l for l in json.loads(lessons.read_text()) if l["jalon"] != milestone]
            lessons.write_text(json.dumps(kept, ensure_ascii=False, indent=2))
        entry.update(resultat="fusionné", fichiers=files, **verdict)
        sh("pkill", "-f", "serve.py")                         # run_forever.sh restarts it on the new code
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
    args = ap.parse_args()
    if args.once:
        sys.exit(0 if repair(ROOT / args.once, args) else 1)
    while True:
        for case in pending(args.tries):
            if spent_today() >= args.daily:
                break
            repair(case, args)
        time.sleep(30)


if __name__ == "__main__":
    main()
