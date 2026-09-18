// Relais public : une source (la machine de jeu), N spectateurs en lecture seule.
//
// Protocole, volontairement minuscule :
//   source → DO   texte  : un événement JSON du harnais (décision, coup, erreur)
//                 binaire : une image PNG de l'écran (160×144)
//   DO → source   texte  : {"spectateurs": n} à chaque changement — la source ne pousse que si n > 0
//   DO → spectateur : les mêmes messages, tels quels, plus {"spectateurs": n}
// Les spectateurs n'envoient RIEN qui atteigne la partie : tout message entrant d'un spectateur est ignoré.

import { DurableObject } from "cloudflare:workers";

const MAX_VIEWERS = 2000;

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (url.pathname === "/ws" || url.pathname === "/push") {
      if (request.headers.get("Upgrade") !== "websocket") return new Response("WebSocket attendu", { status: 426 });
      if (url.pathname === "/push") {
        const given = (request.headers.get("Authorization") || "").replace(/^Bearer /, "");
        if (!env.PUSH_SECRET || !(await sameSecret(given, env.PUSH_SECRET))) return new Response("Interdit", { status: 403 });
      }
      return env.STREAM.get(env.STREAM.idFromName("stream")).fetch(request);
    }
    return env.ASSETS.fetch(request);
  },
};

async function sameSecret(a, b) {              // comparaison à temps constant, via des condensés de même longueur
  const enc = new TextEncoder(), [x, y] = await Promise.all([a, b].map((s) => crypto.subtle.digest("SHA-256", enc.encode(s))));
  const u = new Uint8Array(x), v = new Uint8Array(y);
  let diff = 0;
  for (let i = 0; i < u.length; i++) diff |= u[i] ^ v[i];
  return diff === 0;
}

export class Stream extends DurableObject {
  async fetch(request) {
    const role = new URL(request.url).pathname === "/push" ? "source" : "viewer";
    if (role === "viewer" && this.ctx.getWebSockets("viewer").length >= MAX_VIEWERS) return new Response("Complet", { status: 503 });
    if (role === "source") for (const old of this.ctx.getWebSockets("source")) old.close(1012, "remplacée");   // une seule source

    const [client, server] = Object.values(new WebSocketPair());
    this.ctx.acceptWebSocket(server, [role]);
    this.announce(role === "viewer" ? server : null);
    return new Response(null, { status: 101, webSocket: client });
  }

  announce(newcomer) {
    const viewers = this.ctx.getWebSockets("viewer").length;
    const note = JSON.stringify({ spectateurs: viewers, source: this.ctx.getWebSockets("source").length > 0, nouveau: !!newcomer });
    for (const ws of this.ctx.getWebSockets()) try { ws.send(note); } catch {}
  }

  webSocketMessage(ws, message) {
    if (!this.ctx.getTags(ws).includes("source")) return;           // les spectateurs regardent, ils ne parlent pas
    for (const viewer of this.ctx.getWebSockets("viewer")) try { viewer.send(message); } catch {}
  }

  webSocketClose(ws) { try { ws.close(); } catch {} this.announce(null); }
  webSocketError(ws) { this.webSocketClose(ws); }
}
