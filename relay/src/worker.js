// Relais public : une source (la machine de jeu), N spectateurs en lecture seule.
//
// Protocole, volontairement minuscule :
//   source → DO   texte  : un événement JSON du harnais (décision, coup, erreur)
//                 binaire : une image PNG de l'écran (160×144)
//   DO → source   texte  : {"spectateurs": n} à chaque changement — la source ne pousse que si n > 0
//   DO → spectateur : les mêmes messages, tels quels, plus {"spectateurs": n}
// Les spectateurs n'envoient RIEN qui atteigne la partie. Le chat vit entièrement ici : un message de spectateur est
// validé, rangé en SQLite et rediffusé aux SEULS spectateurs — jamais à la source (ni Jev, ni Claude, ni le réparateur
// ne lisent de texte libre venu du public).
//   spectateur → DO : {"chat": "texte", "pseudo": "nom"}            (+ "admin": secret pour parler en tant qu'hôte)
//                     {"admin": secret, "supprimer": id} · {"admin": secret, "bannir": id}
//   DO → spectateur : {"chat": {id, t, pseudo, texte, hote}} · {"chat_historique": [...]} · {"chat_supprime": [ids]}
//                     {"chat_refus": "pourquoi"} au seul expéditeur

import { DurableObject } from "cloudflare:workers";

const MAX_VIEWERS = 2000;
const EN_PAGE = { title: "Jev plays Pokémon Blue", description: "A model that can only pick among options plays Pokémon Blue, live: every step, its odds, the chat and the harness changelog." };
const CHAT = { texte: 200, pseudo: 20, garde: 300, historique: 60, ecart_ms: 1500, par_minute: 8, global_par_10s: 40 };
const RESERVES = /^(jev|claude|admin|h[oô]te|mod[eé]rateur|syst[eè]me)/i;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (["/ws", "/push", "/chat"].includes(url.pathname)) {   // /chat : le chat seul, sans image ni compte de spectateur
      if (request.headers.get("Upgrade") !== "websocket") return new Response("WebSocket attendu", { status: 426 });
      if (url.pathname === "/push") {
        const given = (request.headers.get("Authorization") || "").replace(/^Bearer /, "");
        if (!env.PUSH_SECRET || !(await sameSecret(given, env.PUSH_SECRET))) return new Response("Interdit", { status: 403 });
      }
      return env.STREAM.get(env.STREAM.idFromName("stream")).fetch(request);
    }
    const english = /^\/en\/?$/.test(url.pathname);                      // /en : la même page, en anglais, avec son aperçu de lien
    const page = await env.ASSETS.fetch(english ? new Request(new URL("/", url), request) : request);
    if (!(page.headers.get("Content-Type") || "").includes("text/html")) return page;
    // les aperçus de lien exigent des adresses absolues : on les écrit ici, la page ne connaît pas son domaine
    return new HTMLRewriter().on('meta[content^="/"]', { element(m) { m.setAttribute("content", url.origin + m.getAttribute("content")); } })
      .on("html", { element(h) { if (english) h.setAttribute("lang", "en"); } })
      .on("title", { element(e) { if (english) e.setInnerContent(EN_PAGE.title); } })
      .on('meta[property="og:title"]', { element(m) { if (english) m.setAttribute("content", EN_PAGE.title); } })
      .on('meta[name="description"], meta[property="og:description"]', { element(m) { if (english) m.setAttribute("content", EN_PAGE.description); } })
      .on('meta[property="og:url"]', { element(m) { if (english) m.setAttribute("content", url.origin + "/en"); } })
      .transform(page);
  },
};

async function sameSecret(a, b) {              // comparaison à temps constant, via des condensés de même longueur
  const enc = new TextEncoder(), [x, y] = await Promise.all([a, b].map((s) => crypto.subtle.digest("SHA-256", enc.encode(s))));
  const u = new Uint8Array(x), v = new Uint8Array(y);
  let diff = 0;
  for (let i = 0; i < u.length; i++) diff |= u[i] ^ v[i];
  return diff === 0;
}

const line = (r) => ({ id: r.id, t: r.t, pseudo: r.pseudo, texte: r.texte, hote: !!r.hote });
const clean = (s) => String(s).replace(/[\u0000-\u001f\u007f\u200b-\u200f\u2028-\u202e\u2066-\u2069]/g, " ").replace(/\s+/g, " ").trim();
async function digest(text) {
  const bytes = new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
  return [...bytes.slice(0, 8)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

export class Stream extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    ctx.storage.sql.exec("CREATE TABLE IF NOT EXISTS chat (id INTEGER PRIMARY KEY AUTOINCREMENT, t INTEGER, pseudo TEXT, texte TEXT, qui TEXT, hote INTEGER)");
    ctx.storage.sql.exec("CREATE TABLE IF NOT EXISTS garde (cle TEXT PRIMARY KEY, valeur TEXT)");
    ctx.storage.sql.exec("CREATE TABLE IF NOT EXISTS bannis (qui TEXT PRIMARY KEY, t INTEGER)");
  }

  async fetch(request) {
    const role = { "/push": "source", "/chat": "chat" }[new URL(request.url).pathname] || "viewer";
    if (role !== "source" && this.ctx.getWebSockets(role).length >= MAX_VIEWERS) return new Response("Complet", { status: 503 });
    if (role === "source") for (const old of this.ctx.getWebSockets("source")) old.close(1012, "remplacée");   // une seule source

    const [client, server] = Object.values(new WebSocketPair());
    this.ctx.acceptWebSocket(server, [role]);
    if (role !== "source") {
      // l'adresse n'est jamais gardée : seulement un condensé salé, pour limiter le débit et bannir
      server.serializeAttachment({ qui: await digest((request.headers.get("CF-Connecting-IP") || "?") + this.env.PUSH_SECRET) });
      const rows = this.ctx.storage.sql.exec("SELECT id, t, pseudo, texte, hote FROM chat ORDER BY id DESC LIMIT ?", CHAT.historique).toArray().reverse();
      try { server.send(JSON.stringify({ chat_historique: rows.map(line) })); } catch {}
      const log = role === "viewer" && this.ctx.storage.sql.exec("SELECT valeur FROM garde WHERE cle = 'changelog'").toArray()[0]?.valeur;   // le changelog du harnais se lit même quand la source dort
      if (log) try { server.send(log); } catch {}
    }
    this.announce(role === "viewer" ? server : null);
    return new Response(null, { status: 101, webSocket: client });
  }

  announce(newcomer) {
    const viewers = this.ctx.getWebSockets("viewer").length;
    const note = JSON.stringify({ spectateurs: viewers, source: this.ctx.getWebSockets("source").length > 0, nouveau: !!newcomer });
    for (const ws of this.ctx.getWebSockets()) try { ws.send(note); } catch {}
  }

  async webSocketMessage(ws, message) {
    if (!this.ctx.getTags(ws).includes("source")) return this.chat(ws, message);   // ne remonte jamais vers la partie
    if (typeof message === "string" && message.startsWith('{"changelog"') && message.length < 120000) this.ctx.storage.sql.exec("INSERT OR REPLACE INTO garde VALUES ('changelog', ?)", message);
    for (const viewer of this.ctx.getWebSockets("viewer")) try { viewer.send(message); } catch {}
  }

  toViewers(note) {
    const raw = JSON.stringify(note);
    for (const role of ["viewer", "chat"]) for (const ws of this.ctx.getWebSockets(role)) try { ws.send(raw); } catch {}
  }

  async chat(ws, message) {
    if (typeof message !== "string" || message.length > 2000) return;
    let note; try { note = JSON.parse(message); } catch { return; }
    if (!note || typeof note !== "object") return;
    const sql = this.ctx.storage.sql, refuse = (why) => { try { ws.send(JSON.stringify({ chat_refus: why })); } catch {} };
    const host = typeof note.admin === "string" && !!this.env.ADMIN_SECRET && await sameSecret(note.admin, this.env.ADMIN_SECRET);

    if (host && (note.supprimer || note.bannir)) {
      const id = Number(note.supprimer || note.bannir), gone = [id];
      if (note.bannir) {
        const row = sql.exec("SELECT qui FROM chat WHERE id = ?", id).toArray()[0];
        if (row) {
          sql.exec("INSERT OR REPLACE INTO bannis VALUES (?, ?)", row.qui, Date.now());
          gone.push(...sql.exec("SELECT id FROM chat WHERE qui = ? AND hote = 0", row.qui).toArray().map((r) => r.id));
          sql.exec("DELETE FROM chat WHERE qui = ? AND hote = 0", row.qui);
        }
      }
      sql.exec("DELETE FROM chat WHERE id = ?", id);
      return this.toViewers({ chat_supprime: [...new Set(gone)] });
    }

    if (typeof note.chat !== "string") return;
    const texte = clean(note.chat).slice(0, CHAT.texte), pseudo = clean(note.pseudo || "").slice(0, CHAT.pseudo) || (note.admin ? "hôte" : "anonyme");
    const { qui } = ws.deserializeAttachment() || {}, now = Date.now();
    if (!texte || !qui) return;
    if (!host) {
      if (sql.exec("SELECT 1 FROM bannis WHERE qui = ?", qui).toArray().length) return refuse("Tu ne peux plus écrire ici.");
      if (RESERVES.test(pseudo)) return refuse("Ce pseudo est réservé.");
      if (/https?:|www\.|\.[a-z]{2,}\//i.test(texte)) return refuse("Pas de lien dans le chat.");
      const mine = sql.exec("SELECT MAX(t) AS dernier, COUNT(*) AS n FROM chat WHERE qui = ? AND t > ?", qui, now - 60000).toArray()[0];
      if (mine.dernier && now - mine.dernier < CHAT.ecart_ms) return refuse("Doucement : un message toutes les deux secondes.");
      if (mine.n >= CHAT.par_minute) return refuse("Doucement : huit messages par minute au plus.");
      if (sql.exec("SELECT COUNT(*) AS n FROM chat WHERE t > ?", now - 10000).toArray()[0].n >= CHAT.global_par_10s) return refuse("Le chat déborde, réessaie dans un instant.");
    }
    const id = sql.exec("INSERT INTO chat (t, pseudo, texte, qui, hote) VALUES (?, ?, ?, ?, ?) RETURNING id", now, pseudo, texte, qui, host ? 1 : 0).one().id;
    sql.exec("DELETE FROM chat WHERE id <= ?", id - CHAT.garde);
    this.toViewers({ chat: line({ id, t: now, pseudo, texte, hote: host ? 1 : 0 }) });
  }

  webSocketClose(ws) { try { ws.close(); } catch {} this.announce(null); }
  webSocketError(ws) { this.webSocketClose(ws); }
}
