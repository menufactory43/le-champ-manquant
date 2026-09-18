# jev-pokemon

Jev joue à Pokémon Bleu (FR). Trois étages, chacun à sa vitesse :

| étage | rôle | cadence | coût mesuré |
|---|---|---|---|
| **harnais** (ce dépôt) | possède la géométrie, les menus, la mémoire | chaque frame | 0 |
| **Jev** (`typesafe-sdk`) | choisit parmi des options nommées, avec cotes | ~450 ms, ~600 tokens | ≈ 0,0025 ¢ / décision (0,041 $/M tokens, tarif déduit du panneau concurrent) |
| **Claude** (CLI `claude -p`, abonnement) | fixe le sous-objectif quand la situation change | ~4 s, ~10 appels / 10 min | ≈ 0,4–0,8 ¢ / appel |

La ROM n'est pas fournie : `pokemon_blue_fr.gb` à la racine, ou `--rom`.

## Lancer

```bash
export TYPESAFE_API_KEY=...
./run_forever.sh                         # vue live http://localhost:8151, anti-veille, relance auto, reprise de live.state
uv run python serve.py --fresh           # repartir de start.state
uv run python serve.py --load saves/20260918-171647_0b_000.state --fresh   # revenir à un point de contrôle
uv run python serve.py --superviseur haiku|sonnet|aucun
uv run python run.py --selftest --rom pokemon_blue_fr.gb --load start.state
```

Sauvegardes : `live.state` + `live.json` (mémoire de l'agent, compteurs) toutes les 10 décisions ;
`saves/` horodaté toutes les 10 min et à chaque badge / nouveau Pokémon (40 derniers gardés) ;
`events.jsonl` = journal complet ; `stuck.jsonl` = boucles détectées.

## Fichiers

- `screen.py` — lit la tilemap `0xC3A0` : boîtes, curseur, texte FR, et l'**écran de quantité** (« ×01 … 200¥ »,
  sans curseur ni boîte de dialogue : le harnais croyait revenir au monde et proposait de marcher).
  Les drapeaux `0xCC28` ne disent pas si un menu est ouvert.
- `ram.py` — état : équipe complète (PV, statut, attaques, PP), adversaire, sac, 190 noms de cartes, drapeau **Pokédex** (`0xD74B` bit 5, vérifié sur les .state). Décalage FR **+5 à partir de `0xCF00`** (validé : `0xCC24–0xCC36` non décalés, `0xCFDA` décalé).
- `data.py` — tables lues **dans la ROM** (donc en français) : attaques `0x38000`, noms d'attaques `0xB0000`, espèces `0x1C21E`, table des types `0x3E489`. Trouvées par recherche de contenu, comme `find_addrs.py`.
- `world.py` — grille de collisions de la carte entière, sorties, personnages (y compris derrière un comptoir), cartes voisines par bord, hautes herbes, BFS.
- `battle.py` — combat en intentions : `attaque_N` (type, puissance, efficacité, et les **PV que le coup retire**
  — formule de dégâts de la 1ʳᵉ génération sur les statistiques du moment, Attaque/Spécial lus dans `wBattleMon` :
  « peu efficace » disait la même chose de GRIFFE (2 PV) et de FLAMMECHE (9 PV) contre le RACAILLOU de Pierre),
  `changer_N`, `capturer`, `potion`, `fuite`, `choisir_N`. Macros vérifiées au curseur (`0xCC24/25/26`). Testé : attaque, fuite. **Non testé en jeu : capturer, potion, changer.**
- `route.py` — carnet de jalons + **atlas** : les cartes traversées, mais aussi les destinations *lues* dans la table
  des warps et des bords (une porte jamais franchie reste un fait). D'où le cap affiché sur chaque sortie, et la liste
  des cartes connues jamais explorées transmise au superviseur quand la zone du jalon n'est pas encore atteignable.
- `agent.py` — espace d'actions contextuel ; texte pur avancé sans requête ; action sans effet retirée sur place ;
  le jeu garde parfois la manette (`0xD730` bits 0/7 : un personnage ou le héros marche un script) — on ne demande
  rien à Jev tant qu'elle n'est pas rendue, et un plan de marche s'arrête dès qu'un pas ne tombe pas où il devait
  (sinon la suite du chemin se joue depuis la mauvaise case) ; une sortie tentée sans jamais arriver et une carte
  où l'équipe est tombée K.O. sont dites dans l'option elle-même ; déclencheurs du superviseur (début, badge/Pokémon/objet, zone atteinte, équipe affaiblie, stagnation 14 → 28 → … décisions).
  Toutes les questions à Jev passent par un **lien patient** : une coupure réseau (DNS, timeout, 429, 5xx) n'est pas une
  décision à prendre — le harnais attend en faisant tourner l'émulateur (la vue live continue), réessaie 6 fois, et le
  champ `hors_ligne` le dit. Une direction ne peut nommer que trois destinations : celle qui mène à la zone visée
  passe devant les plus proches (à Argenta, « bord est vers Route 3 » était dixième sur douze, noyé dans
  « (et 9 autres) »), et la marque « zone visée » s'affiche aussi quand cette zone est la carte voisine elle-même. Une panne de lien n'est jamais comptée comme un bug du harnais : elle ne fige plus de cas.
  Une panne a une **identité** (`empreinte` = classe d'exception + fonction du harnais où elle casse), pas seulement un
  libellé : les répétitions se comptent dessus et un même incident n'est gelé qu'une fois pour le réparateur.
  Décrire la situation est le travail du harnais, donc c'est le harnais qui peut se tromper : si la description casse,
  **les sept touches brutes restent proposées** (`RAW_BUTTONS`), la panne est dite à Jev dans l'état (`panne_harnais`,
  visible en direct) et gelée une fois au 3ᵉ passage — au lieu de ressortir de `step` et de rejouer le même plantage
  toutes les deux secondes sur une partie qui n'avance plus jamais. Les destinations d'une direction sont des `Lead`
  nommés : un champ ajouté ne casse plus les cinq endroits qui les relisaient par position.
- `supervisor.py` — Claude via le CLI, dépouillé (`--system-prompt`, sans outils/MCP/réglages, `MAX_THINKING_TOKENS=0`) : 71 k → 1,5 k tokens par appel.
- `serve.py` + `ui.html` — vue live (canvas), cotes, équipe, compteurs Jev et Claude, objectif forçable, pause, vitesse ×1/×3/max.

## Mesures superviseur (18/09/2026, 4 situations pièges, 1 essai chacune)

| | latence | coût / appel | remarques |
|---|---|---|---|
| Haiku, réflexion par défaut | 25–60 s, 1 timeout | 1,8 ¢ | meilleur raisonnement |
| Haiku, sans réflexion | 3,5 s | 0,4 ¢ | 2 erreurs de géographie en ~12 appels réels (lieu inventé) |
| Sonnet, sans réflexion | 3–4 s | 0,3–0,8 ¢ | défaut retenu |

Un appel CLI « nu » coûte 14,6 ¢ (prompt système + skills + MCP) : ne jamais l'utiliser tel quel.

## Tourner 24 h/24

Sur une machine Linux toujours allumée : `run_forever.sh --host 0.0.0.0` dans un service systemd utilisateur
(`Restart=always`, linger activé). La clé TypeSafe va dans `.env` (`TYPESAFE_API_KEY=...`, droits 600) et le CLI `claude`
doit y être connecté. Ne lancez pas deux instances sur la même sauvegarde : deux parties, double dépense.

## Journal de construction

Cette partie a le droit d'être aidée (Claude, réparateur) ; son produit est `journal/` : chaque décision avec le contexte
exact envoyé à Jev, les options offertes et celles retirées par le harnais, les cotes, l'effet du coup ; chaque texte du
jeu ; chaque repérage de jalon ; chaque intervention de Claude avec le nombre de décisions avant un progrès ; un banc
d'essai (`journal/bancs/<jalon>.state`) au début de chaque jalon. `uv run python bilan.py > bilan.md` en tire la liste de
travail du harnais. Le vrai test : une seconde partie depuis `start.state`, `--superviseur aucun`, réparateur coupé.

## Page publique (relais Cloudflare)

`relay/` : Worker `jev-pokemon` + un Durable Object SQLite `Stream` (WebSocket Hibernation), offre gratuite. La machine de jeu ouvre un
seul WebSocket **sortant** (`relay.py`, activé par `RELAY_URL` et `RELAY_SECRET` dans `.env`) : il n'est jamais exposé.
Il ne pousse images (PNG 160×144, ~2 Ko) et événements que s'il y a au moins un spectateur ; à vide le relais hiberne,
ce qui protège le quota gratuit partagé avec les autres relais du compte. La page publique est `ui.html` en mode
WebSocket, en lecture seule (aucun message de spectateur n'atteint la partie).

Le relais garde le dernier changelog du harnais (poussé par la machine de jeu à la connexion et à chaque changement) et le sert à tout arrivant, même quand la source dort.

**Anglais.** La page existe en français et en anglais (`?lang=en`, ou `/en` sur le relais pour un aperçu de lien anglais ; par défaut selon la langue du navigateur, bascule FR/EN dans l'en-tête). Seul l'habillage est traduit : Jev joue la cartouche française, donc textes du jeu, options et objectifs restent tels qu'il les lit. Les entrées du changelog portent `titre_en`, `cause_en`, `resume_en` (le réparateur les écrit lui-même).

**Chat.** Les spectateurs se parlent dans un cadre `[ Chat ]` qui vit entièrement dans le relais : un message est validé
(200 caractères, pas de lien, un message toutes les 1,5 s et 8 par minute par condensé d'adresse — l'adresse elle-même
n'est jamais gardée), rangé en SQLite (300 derniers) et rediffusé aux **seuls** spectateurs. Rien ne remonte vers la
source : ni Jev, ni Claude, ni le réparateur ne lisent de texte libre venu du public. Modération : ouvrir une fois la page
avec `?admin=<ADMIN_SECRET>` (le secret passe en mémoire locale et disparaît de la barre d'adresse) ; chaque message
porte alors ✖ (supprimer) et ⊘ (bannir l'auteur et retirer ses messages), et l'hôte écrit avec une marque ◆. La vue
privée affiche le même chat si on lui donne une fois `?relay=<hôte du relais>` (socket `/chat` : ni image, ni
compte de spectateur).

```bash
cd relay && cp ../ui.html public/index.html && npx wrangler deploy
openssl rand -hex 32 | tee /dev/stderr | npx wrangler secret put PUSH_SECRET     # même valeur dans RELAY_SECRET sur la machine de jeu
openssl rand -hex 32 | tee /dev/stderr | npx wrangler secret put ADMIN_SECRET    # modération du chat
```

## Réparateur autonome

`repair.py` (lancé par `run_forever.sh`) surveille `stuck/`. Un cas y est figé quand Claude a échoué 3 fois à débloquer
un jalon (« trou de harnais »), quand la même exception se répète 5 fois, ou — sans attendre Claude — dès que Jev
piétine 30 décisions de marche libre sur 3 cases au plus (« boucle »). Une session Claude Code (Opus, CLI) diagnostique
et patche ; **le script, pas Claude, décide** : périmètre (perception et actions uniquement, jamais `carnet.json`,
`replay.py`, `repair.py`), compilation, `replay.py` du cas, non-régression sur les cas résolus. Passe → commit git,
`changelog.jsonl`, purge des leçons du jalon, redémarrage. Échoue → `git checkout`. Garde-fous (équivalent tarif catalogue, l'abonnement ne facture rien) :
10 $ / intervention, 100 $ / jour, 3 essais / cas, 1 h maximum. Dépenses dans `repairs.jsonl` (hors coût de la partie).
Avant de payer un diagnostic, le script rejoue le cas avec le harnais du jour : s'il en sort déjà (corrigé entre-temps),
le cas est clos pour 0 $. Sinon la trace de cette reproduction ouvre la demande, avec les 40 dernières décisions de Jev
(options, options retirées, cotes, effet : `stuck/<cas>/decisions.jsonl`), les essais précédents sur ce cas (motif du
rejet et patch conservé dans `essai-N.diff` : on ne repart jamais de zéro) et les réparations déjà fusionnées sur le même
jalon. Une panne du CLI n'est pas un essai. Un ancien cas ne sert de témoin de non-régression que si le harnais du jour, sans le patch, en sort encore. Après une fusion, le script **suit la partie officielle** : « sorti en direct
n décisions après la fusion » ou « aucun progrès en 150 décisions » (champ `suivi` de `repairs.jsonl`) — un replay qui
passe est un résultat de laboratoire, celui-ci est le résultat de terrain.
Premier cas, 18/09/2026 : drapeau « Pokédex reçu » `0xD74B` bit 5 — 2 min 55 s, 1,10 $.

## Ce qui manque pour aller au bout

CS hors combat (COUPE, SURF, FORCE, FLASH via le menu), vente d'objets, PC, rebords à sens unique,
énigmes (dalles du Repaire Rocket, téléporteurs Sylphe, rochers de la Route Victoire), Parc Safari.

## Le principe

Une boucle n'est pas un signe que le modèle est bête : c'est un signe qu'il **manque un champ dans l'état**
ou une action dans le menu. `stuck.jsonl` est la liste de ce qu'il faut ajouter.
