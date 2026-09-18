"""Pokémon Red/Blue WRAM map. Addresses follow the pret/pokered disassembly (English);
`shifted_reader` moves them for localised ROMs."""
from __future__ import annotations

import data, screen

ADDR = {
    "map_id": 0xD35E, "x": 0xD362, "y": 0xD361,
    "party_count": 0xD163, "party_mons": 0xD16B,      # 44 bytes per Pokémon
    "badges": 0xD356, "in_battle": 0xD057,            # 1 wild, 2 trainer
    "money": 0xD347,                                  # 3 bytes, binary-coded decimal
    "bag_count": 0xD31D, "bag_items": 0xD31E,         # (id, quantity) pairs
    "enemy_mon": 0xCFE5, "battle_mon": 0xD014, "battle_pp": 0xD02D,
    "cursor_row": 0xCC26, "cursor_x": 0xCC25, "cursor_y": 0xCC24, "list_scroll": 0xCC36,
}
# Drapeaux d'événement (wEventFlags 0xD747). Le Pokédex n'est ni un objet du sac ni un
# badge : sans ce bit, rien dans l'état ne dit que la trame a passé le labo.
# Vérifié sur les .state : 0 tant que le COLIS est au sac, 1 dès « obtient le POKéDEX! ».
POKEDEX_FLAG = (0xD74B, 5)
# Le jeu garde la manette : bit 0 = un personnage marche un script, bit 7 = c'est le héros
# qui est déplacé par le jeu. Dans ces deux cas les touches sont avalées.
# Vérifié sur stuck/0005 : 0x01 pendant que le garçon d'Argenta s'avance (aucune touche ne
# passe), 0x80 pendant qu'il ramène le héros vers l'arène, 0x00 partout ailleurs
# (start.state, live.state, combats de stuck/0003 et 0004).
SCRIPT_CONTROL = (0xD730, 0x81)
SHIFT_FROM = 0xCF00      # validated: 0xCC24-0xCC36 unshifted, 0xCFDA and above shifted

_TOWNS = ["Bourg Palette", "Jadielle", "Argenta", "Azuria", "Lavanville", "Carmin-sur-Mer",
          "Céladopole", "Parmanie", "Cramois'Île", "Plateau Indigo", "Safrania"]
MAPS = {i: n for i, n in enumerate(_TOWNS)}
MAPS.update({11 + n: f"Route {n}" for n in range(1, 26)})
MAPS.update({
    37: "Maison du héros (RDC)", 38: "Maison du héros (étage)", 39: "Maison du rival",
    40: "Labo du Prof. Chen", 41: "Centre Pokémon de Jadielle", 42: "Boutique de Jadielle",
    43: "École de Jadielle", 44: "Maison de Jadielle", 45: "Arène de Jadielle",
    46: "Cave Taupiqueur (Route 2)", 47: "Poste nord de la Forêt de Jade", 48: "Maison d'échange (Route 2)",
    49: "Poste de la Route 2", 50: "Poste sud de la Forêt de Jade", 51: "Forêt de Jade",
    52: "Musée d'Argenta (RDC)", 53: "Musée d'Argenta (étage)", 54: "Arène d'Argenta",
    55: "Maison d'Argenta", 56: "Boutique d'Argenta", 57: "Maison d'Argenta", 58: "Centre Pokémon d'Argenta",
    59: "Mont Sélénite 1F", 60: "Mont Sélénite B1F", 61: "Mont Sélénite B2F",
    62: "Maison cambriolée d'Azuria", 63: "Maison d'échange d'Azuria", 64: "Centre Pokémon d'Azuria",
    65: "Arène d'Azuria", 66: "Boutique de vélos", 67: "Boutique d'Azuria", 68: "Centre Pokémon du Mont Sélénite",
    70: "Poste de la Route 5", 71: "Souterrain (entrée Route 5)", 72: "Pension Pokémon",
    73: "Poste de la Route 6", 74: "Souterrain (entrée Route 6)", 76: "Poste de la Route 7",
    77: "Souterrain (entrée Route 7)", 79: "Poste de la Route 8", 80: "Souterrain (entrée Route 8)",
    81: "Centre Pokémon de la Grotte", 82: "Grotte 1F", 83: "Centrale", 84: "Poste de la Route 11",
    85: "Cave Taupiqueur (Route 11)", 87: "Poste de la Route 12", 88: "Maison de Léo",
    89: "Centre Pokémon de Carmin", 90: "Fan Club Pokémon", 91: "Boutique de Carmin", 92: "Arène de Carmin",
    94: "Quai de Carmin", 95: "Océane 1F", 96: "Océane 2F", 97: "Océane 3F", 98: "Océane B1F",
    99: "Océane pont", 100: "Océane cuisine", 101: "Océane cabine du capitaine",
    108: "Route Victoire 1F", 113: "Salle de Peter", 118: "Panthéon", 119: "Souterrain nord-sud",
    120: "Salle du Maître", 121: "Souterrain ouest-est",
    122: "Centre commercial 1F", 123: "Centre commercial 2F", 124: "Centre commercial 3F",
    125: "Centre commercial 4F", 126: "Centre commercial toit", 133: "Centre Pokémon de Céladopole",
    134: "Arène de Céladopole", 135: "Casino", 136: "Centre commercial 5F", 141: "Centre Pokémon de Lavanville",
    142: "Tour Pokémon 1F", 143: "Tour Pokémon 2F", 144: "Tour Pokémon 3F", 145: "Tour Pokémon 4F",
    146: "Tour Pokémon 5F", 147: "Tour Pokémon 6F", 148: "Tour Pokémon 7F", 149: "Maison de M. Fuji",
    150: "Boutique de Lavanville", 152: "Boutique de Parmanie", 154: "Centre Pokémon de Parmanie",
    155: "Maison du Gardien", 156: "Entrée du Parc Safari", 157: "Arène de Parmanie",
    159: "Îles Écume B1F", 160: "Îles Écume B2F", 161: "Îles Écume B3F", 162: "Îles Écume B4F",
    165: "Manoir Pokémon 1F", 166: "Arène de Cramois'Île", 167: "Labo de Cramois'Île",
    171: "Centre Pokémon de Cramois'Île", 172: "Boutique de Cramois'Île", 174: "Hall du Plateau Indigo",
    177: "Dojo de Safrania", 178: "Arène de Safrania", 180: "Boutique de Safrania", 181: "Sylphe SARL 1F",
    182: "Centre Pokémon de Safrania", 192: "Îles Écume 1F", 194: "Route Victoire 2F", 197: "Cave Taupiqueur",
    198: "Route Victoire 3F", 199: "Repaire Rocket B1F", 200: "Repaire Rocket B2F", 201: "Repaire Rocket B3F",
    202: "Repaire Rocket B4F", 207: "Sylphe SARL 2F", 208: "Sylphe SARL 3F", 209: "Sylphe SARL 4F",
    210: "Sylphe SARL 5F", 211: "Sylphe SARL 6F", 212: "Sylphe SARL 7F", 213: "Sylphe SARL 8F",
    214: "Manoir Pokémon 2F", 215: "Manoir Pokémon 3F", 216: "Manoir Pokémon B1F",
    217: "Parc Safari est", 218: "Parc Safari nord", 219: "Parc Safari ouest", 220: "Parc Safari centre",
    226: "Caverne Azurée 2F", 227: "Caverne Azurée B1F", 228: "Caverne Azurée 1F", 232: "Grotte B1F",
    233: "Sylphe SARL 9F", 234: "Sylphe SARL 10F", 235: "Sylphe SARL 11F",
    245: "Salle d'Olga", 246: "Salle d'Aldo", 247: "Salle d'Agatha",
})
POKEMON_CENTERS = {41, 58, 64, 68, 81, 89, 133, 141, 154, 171, 174, 182}
# Boutiques : même convention que les Centres, le personnage n°1 tient le comptoir
# (vérifié sur la Boutique d'Argenta, carte 56 : lui parler ouvre ACHETER / VENDRE).
MARTS = {42, 56, 67, 91, 150, 152, 172, 180}

BADGE_NAMES = ["Roche", "Cascade", "Foudre", "Prisme", "Âme", "Marais", "Volcan", "Terre"]
STATUS = ((0x07, "endormi"), (0x08, "empoisonné"), (0x10, "brûlé"), (0x20, "gelé"), (0x40, "paralysé"))


def _u16(read, address: int) -> int:
    return (read(address) << 8) | read(address + 1)


def _bcd3(read, address: int) -> int:
    total = 0
    for offset in range(3):
        byte = read(address + offset)
        total = total * 100 + (byte >> 4) * 10 + (byte & 0x0F)
    return total


def shifted_reader(read, shift: int):
    """Localised ROMs move the upper WRAM block."""
    return lambda a: read(a + shift if a >= SHIFT_FROM else a)


def plausible(read) -> bool:
    return (read(ADDR["party_count"]) <= 6 and read(ADDR["map_id"]) < 248
            and _bcd3(read, ADDR["money"]) <= 999999)


def detect_shift(read, candidates=(0, 5)) -> int:
    """Pick the offset that yields a state the game could actually be in."""
    for shift in candidates:
        if plausible(shifted_reader(read, shift)):
            return shift
    return 0


def _status(byte: int) -> str | None:
    return next((name for mask, name in STATUS if byte & mask), None)


def _moves(read, ids_at: int, pp_at: int) -> list[dict]:
    out = []
    for slot in range(4):
        move = data.move(read(ids_at + slot))
        if move:
            out.append({**move, "pp": read(pp_at + slot) & 0x3F, "case": slot + 1})
    return out


def _party_mon(read, index: int) -> dict:
    base = ADDR["party_mons"] + 44 * index
    return {"n": index + 1, "espece": data.species(read(base)), "niveau": read(base + 0x21),
            "pv": _u16(read, base + 1), "pv_max": _u16(read, base + 0x22),
            "statut": _status(read(base + 4)),
            "types": sorted({data.TYPES.get(read(base + 5), "?"), data.TYPES.get(read(base + 6), "?")}),
            "attaques": _moves(read, base + 8, base + 0x1D)}


def _battle(read) -> dict:
    enemy, mine = ADDR["enemy_mon"], ADDR["battle_mon"]
    return {
        "adversaire": {"espece": data.species(read(enemy)), "niveau": read(enemy + 0x0E),
                       "pv": _u16(read, enemy + 1), "pv_max": _u16(read, enemy + 0x0F),
                       "statut": _status(read(enemy + 4)),
                       "type_ids": [read(enemy + 5), read(enemy + 6)],
                       "defense": _u16(read, enemy + 0x13), "special": _u16(read, enemy + 0x17),
                       "types": sorted({data.TYPES.get(read(enemy + 5), "?"), data.TYPES.get(read(enemy + 6), "?")})},
        # Mêmes décalages que la structure adverse (déjà validés pour sa Défense et son Spécial) :
        # sans l'Attaque et le Spécial du Pokémon actif, rien ne permet de chiffrer un coup.
        # Vérifié sur le combat d'arène rejoué depuis stuck/0008 : SALAMECHE niv.17 → 25/26/33/27,
        # et les dégâts calculés avec ces valeurs tombent sur ceux que le jeu retire (2 et 9 PV).
        "actif": {"espece": data.species(read(mine)), "niveau": read(mine + 0x0E),
                  "pv": _u16(read, mine + 1), "pv_max": _u16(read, mine + 0x0F),
                  "statut": _status(read(mine + 4)),
                  "type_ids": [read(mine + 5), read(mine + 6)],
                  "attaque": _u16(read, mine + 0x11), "defense": _u16(read, mine + 0x13),
                  "special": _u16(read, mine + 0x17),
                  "attaques": _moves(read, mine + 8, ADDR["battle_pp"])},
    }


def read_state(read) -> dict:
    """`read` takes an address and returns one byte."""
    map_id = read(ADDR["map_id"])
    badges_bits = read(ADDR["badges"])
    in_battle = read(ADDR["in_battle"])
    ui = screen.read_ui(read)
    party = [_party_mon(read, i) for i in range(min(read(ADDR["party_count"]), 6))]
    bag = [{"id": read(ADDR["bag_items"] + 2 * i), "quantite": read(ADDR["bag_items"] + 2 * i + 1)}
           for i in range(min(read(ADDR["bag_count"]), 20))]
    for item in bag:
        item["nom"] = data.ITEMS.get(item["id"], f"objet {item['id']}")
    return {
        "carte": MAPS.get(map_id, f"carte {map_id}"), "carte_id": map_id,
        "position": {"x": read(ADDR["x"]), "y": read(ADDR["y"])},
        "orientation": {0x00: "down", 0x04: "up", 0x08: "left", 0x0C: "right"}.get(read(0xC109) & 0x0C),
        "en_combat": in_battle in (1, 2),
        "type_combat": {1: "sauvage", 2: "dresseur"}.get(in_battle),
        "combat": _battle(read) if in_battle in (1, 2) else None,
        "scene_scriptee": bool(read(SCRIPT_CONTROL[0]) & SCRIPT_CONTROL[1]),
        "texte_affiche": ui["dialogue"] is not None, "dialogue": ui["dialogue"],
        "menu_ouvert": ui["menu"] is not None, "menu": ui["menu"], "liste_attaques": ui["liste_attaques"],
        "quantite": ui["quantite"],
        "curseur": {"ligne": read(ADDR["cursor_row"]), "x": read(ADDR["cursor_x"]),
                    "y": read(ADDR["cursor_y"]), "defilement": read(ADDR["list_scroll"])},
        "equipe": party,
        "badges": [name for i, name in enumerate(BADGE_NAMES) if badges_bits & (1 << i)],
        "argent": _bcd3(read, ADDR["money"]), "sac": bag,
        "pokedex": bool(read(POKEDEX_FLAG[0]) & (1 << POKEDEX_FLAG[1])),
    }


def signature(state: dict) -> tuple:
    """What counts as 'the same situation' for loop detection."""
    # facing is part of the situation: turning toward someone changes nothing else, and it is not a wasted move
    return (state["carte_id"], state["position"]["x"], state["position"]["y"], state.get("orientation"),
            state["en_combat"], state["dialogue"],
            state["menu"]["ligne"] if state["menu"] else None,
            state["quantite"]["nombre"] if state["quantite"] else None,
            state["combat"]["adversaire"]["pv"] if state["combat"] else None)
