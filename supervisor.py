"""The slow brain. Jev picks among options; Claude decides what we are trying to do next.

Runs through the `claude` CLI (subscription, no API key), stripped of tools, MCP servers
and settings so a call carries ~1.5k tokens instead of ~70k. Every call is metered.
"""
from __future__ import annotations
import json, os, re, subprocess, threading, time

GUIDE = """Tu supervises un agent qui joue à Pokémon Bleu (version française). L'agent ne sait PAS planifier :
à chaque instant il choisit parmi des actions locales proposées par le harnais — prendre une sortie nommée
(porte, escalier), quitter la carte par un bord (nord/sud/est/ouest), parler à un personnage numéroté,
interagir devant lui, et en combat choisir une attaque, changer, capturer ou fuir. Il voit les noms des
cartes voisines. Ton rôle : lui donner le PROCHAIN sous-objectif, court, concret, atteignable en quelques
minutes, formulé avec ces actions et des noms de lieux français. Objectif final : battre la Ligue Pokémon
le plus vite possible.

Le jalon en cours du carnet de route t'est donné dans « jalon_du_carnet » : reste dans ce jalon.

Lieux de soin (il n'y en a PAS ailleurs) : à Bourg Palette, parler à la maman dans « Maison du héros (RDC) » ; ensuite les
Centres Pokémon de Jadielle, Argenta, Mont Sélénite (Route 4), Azuria, Carmin-sur-Mer, Lavanville, Céladopole, Parmanie,
Safrania, Cramois'Île, Plateau Indigo. N'invente jamais un lieu : n'utilise que des noms présents dans l'état, dans
« actions_possibles_maintenant » ou dans le jalon. Lis « journal_recent » : si l'agent fait des allers-retours entre
deux cartes, ton dernier sous-objectif était faux ou ambigu — corrige-le.

Règles : si l'équipe est affaiblie (PV bas, K.O.), le sous-objectif est d'aller soigner au Centre Pokémon le plus proche
(parler à l'infirmière, répondre OUI). Si l'agent stagne, propose une AUTRE approche que les sous-objectifs déjà tentés.
Ne suppose jamais un événement accompli s'il n'apparaît pas dans l'état (objets du sac, badges, équipe).

Réponds UNIQUEMENT avec un objet JSON brut, sans balises :
{"sous_objectif": "<une ou deux phrases impératives>", "prochaine_zone": "<nom de carte visé>", "raison": "<une phrase>",
 "lecon": "<un fait de jeu général et durable que l'agent ignorait et qui explique ce blocage, ou chaîne vide>"}"""


class Supervisor:
    def __init__(self, model: str = "sonnet", timeout: int = 60):
        self.model, self.timeout = model, timeout
        self.stats = {"appels": 0, "echecs": 0, "cout_usd": 0.0, "tokens_entree": 0,
                      "tokens_sortie": 0, "ms": 0, "modele": model}
        self.busy = False
        self.result: dict | None = None
        self._lock = threading.Lock()

    def ask(self, context: dict) -> dict | None:
        """Blocking call. Returns {"sous_objectif", "prochaine_zone", "raison"} or None."""
        started = time.perf_counter()
        try:
            out = subprocess.run(
                ["claude", "-p", "--model", self.model, "--output-format", "json",
                 "--system-prompt", GUIDE, "--tools", "", "--strict-mcp-config",
                 "--mcp-config", '{"mcpServers":{}}', "--setting-sources", "",
                 "--disable-slash-commands", "--no-session-persistence"],
                input=json.dumps(context, ensure_ascii=False), capture_output=True, text=True,
                timeout=self.timeout, cwd="/tmp",
                env={**os.environ, "MAX_THINKING_TOKENS": "0"})      # measured: thinking costs 5x, takes 10x
            reply = json.loads(out.stdout)
            usage = reply.get("usage", {})
            self.stats["appels"] += 1
            self.stats["cout_usd"] += reply.get("total_cost_usd", 0.0)
            self.stats["tokens_entree"] += (usage.get("input_tokens", 0) + usage.get("cache_read_input_tokens", 0)
                                            + usage.get("cache_creation_input_tokens", 0))
            self.stats["tokens_sortie"] += usage.get("output_tokens", 0)
            self.stats["ms"] = round((time.perf_counter() - started) * 1000)
            found = re.search(r"\{.*\}", reply.get("result", ""), re.S)
            answer = json.loads(found.group(0)) if found else None
            return answer if answer and answer.get("sous_objectif") else None
        except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError):
            self.stats["echecs"] += 1
            return None

    def ask_async(self, context: dict) -> bool:
        """Fire and forget; the game keeps running. Collect with `take()`."""
        with self._lock:
            if self.busy:
                return False
            self.busy = True

        def work():
            answer = self.ask(context)
            with self._lock:
                self.result, self.busy = answer, False
        threading.Thread(target=work, daemon=True).start()
        return True

    def take(self) -> dict | None:
        with self._lock:
            answer, self.result = self.result, None
            return answer
