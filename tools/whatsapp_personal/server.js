/**
 * WhatsApp Personal Bridge HTTP Server.
 *
 * Connects directly to WhatsApp Multi-Device protocol using Baileys.
 * Provides both:
 *   1. Real live QR code generation (scannable via WhatsApp on phone)
 *   2. Phone number pairing code (for "Link with phone number instead" without camera)
 *
 * Endpoints:
 *   GET  /api/whatsapp-personal/status        → connection state, phone number, hasQr
 *   GET  /api/whatsapp-personal/qr            → current live QR code string
 *   POST /api/whatsapp-personal/pairing-code  → generate 8-character WhatsApp pairing code
 *   POST /api/whatsapp-personal/disconnect    → unlink account & reset credentials
 *   GET  /api/whatsapp-personal/messages      → recent inbound/outbound interaction log
 *   POST /api/whatsapp-personal/simulate      → test AI reply generation
 *   GET  /health                              → server health status
 */

import {
  makeWASocket,
  useMultiFileAuthState,
  DisconnectReason,
  fetchLatestBaileysVersion,
} from '@whiskeysockets/baileys';
import pino from 'pino';
import http from 'http';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import dotenv from 'dotenv';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Load root .env
dotenv.config({ path: path.resolve(__dirname, '../../.env') });

const AUTH_DIR = process.env.WA_AUTH_DIR || path.resolve(__dirname, 'auth_info_baileys');
const DATA_DIR = path.dirname(AUTH_DIR);
const MESSAGES_FILE = path.join(DATA_DIR, 'messages.json');
const STATUS_FILE = path.join(DATA_DIR, 'conversations_status.json');
const PORT = parseInt(process.env.WA_BRIDGE_PORT || '3100', 10);
const logger = pino({ level: 'silent' });

// ─── Shared State ────────────────────────────────────────────────────────────

let currentQr = null;                  // Real QR string from WhatsApp Web socket
let connectionStatus = 'initializing'; // initializing | waiting_for_scan | connected | disconnected
let connectedPhone = null;             // e.g. "919019527568"
let sock = null;
let reconnectTimer = null;
let isStarting = false;

// In-memory conversation context per phone number
const conversationMemory = new Map();

// Conversation status tracking (open | closed)
const conversationStatusMap = new Map();

// In-memory message log (recent inbound and outbound)
const messageLog = [
  {
    id: "msg-init-1",
    direction: "inbound",
    phone: "+91 90195 27568",
    senderName: "Abhay Pharma",
    message: "Hi, we are evaluating OpsGrids for our pharma operations. Do you handle both CRM and inventory?",
    timestamp: new Date(Date.now() - 15 * 60000).toISOString(),
    status: "received",
  },
  {
    id: "msg-init-2",
    direction: "outbound",
    phone: "+91 90195 27568",
    senderName: "OpsGrids AI",
    message: "Hi! Yes, OpsGrids provides connected CRM, inventory, invoicing, and attendance with single sign-in. Would you like a brief 10-minute walkthrough this week?",
    timestamp: new Date(Date.now() - 14 * 60000).toISOString(),
    status: "ai_replied",
  },
];
const MAX_LOG = 200;

function loadPersistedState() {
  try {
    if (fs.existsSync(MESSAGES_FILE)) {
      const data = fs.readFileSync(MESSAGES_FILE, 'utf8');
      const loaded = JSON.parse(data);
      if (Array.isArray(loaded) && loaded.length > 0) {
        messageLog.length = 0;
        messageLog.push(...loaded);
      }
    }
  } catch (err) {
    console.error('[!] Failed to load messages.json:', err.message);
  }

  try {
    if (fs.existsSync(STATUS_FILE)) {
      const data = fs.readFileSync(STATUS_FILE, 'utf8');
      const loaded = JSON.parse(data);
      for (const [k, v] of Object.entries(loaded)) {
        conversationStatusMap.set(k, v);
      }
    }
  } catch (err) {
    console.error('[!] Failed to load conversations_status.json:', err.message);
  }
}

function persistMessages() {
  try {
    if (!fs.existsSync(DATA_DIR)) {
      fs.mkdirSync(DATA_DIR, { recursive: true });
    }
    fs.writeFileSync(MESSAGES_FILE, JSON.stringify(messageLog, null, 2), 'utf8');
  } catch (err) {
    console.error('[!] Failed to persist messages.json:', err.message);
  }
}

function persistConversationStatus() {
  try {
    if (!fs.existsSync(DATA_DIR)) {
      fs.mkdirSync(DATA_DIR, { recursive: true });
    }
    const obj = Object.fromEntries(conversationStatusMap);
    fs.writeFileSync(STATUS_FILE, JSON.stringify(obj, null, 2), 'utf8');
  } catch (err) {
    console.error('[!] Failed to persist conversations_status.json:', err.message);
  }
}

function pushMessage(entry) {
  messageLog.unshift(entry);
  if (messageLog.length > MAX_LOG) messageLog.length = MAX_LOG;
  persistMessages();
}

function buildConversationsList(statusFilter) {
  const convMap = new Map();
  // Traverse messageLog backwards so earlier messages set created_at and later ones set last_customer_message_at
  const reversed = [...messageLog].reverse();
  for (const m of reversed) {
    const rawDigits = (m.phone || '').replace(/[^0-9]/g, '');
    if (!rawDigits) continue;
    const convId = 'wa-conv-' + rawDigits;
    const isClosed = conversationStatusMap.get(convId) === 'closed';
    const status = isClosed ? 'closed' : 'open';
    const mTime = m.timestamp || new Date().toISOString();

    const existing = convMap.get(convId);
    if (!existing) {
      convMap.set(convId, {
        id: convId,
        wa_address: m.phone.startsWith('+') ? m.phone : '+' + m.phone,
        channel: 'whatsapp',
        status,
        last_customer_message_at: mTime,
        within_service_window: true,
        contact_id: null,
        assignee_membership_id: null,
        created_at: mTime,
        updated_at: mTime,
        profile_name: m.senderName || null,
      });
    } else {
      existing.updated_at = mTime;
      existing.last_customer_message_at = mTime;
      if (m.senderName && m.direction === 'inbound') {
        existing.profile_name = m.senderName;
      }
    }
  }

  let items = Array.from(convMap.values());
  if (statusFilter) {
    items = items.filter((c) => c.status === statusFilter);
  }
  items.sort(
    (a, b) =>
      new Date(b.last_customer_message_at).getTime() -
      new Date(a.last_customer_message_at).getTime(),
  );
  return items;
}

// ─── AI Reply Engine ─────────────────────────────────────────────────────────

async function generateAIReply(incomingText, senderPhone) {
  const geminiKey = process.env.GEMINI_API_KEY;
  const openaiKey = process.env.OPENAI_API_KEY;

  const history = conversationMemory.get(senderPhone) || [];
  history.push({ role: 'user', content: incomingText });
  if (history.length > 10) history.shift();

  const systemPrompt = `You are a helpful customer operations AI assistant for OpsGrids.
You are replying to a customer on WhatsApp.
Keep your response short, conversational, polite, and concise (1 to 2 sentences).
Do not use markdown headers or lengthy corporate email templates.
OpsGrids helps companies unify CRM, Attendance, Invoicing, and Inventory on one platform.`;

  // 1. Google Gemini
  if (geminiKey) {
    try {
      const model = process.env.GEMINI_MODEL || 'gemini-1.5-flash';
      const url = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${geminiKey}`;
      const contents = history.map((h) => ({
        role: h.role === 'assistant' ? 'model' : 'user',
        parts: [{ text: h.content }],
      }));

      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          system_instruction: { parts: [{ text: systemPrompt }] },
          contents,
          generationConfig: { temperature: 0.3, maxOutputTokens: 250 },
        }),
      });

      if (res.ok) {
        const data = await res.json();
        const reply = data?.candidates?.[0]?.content?.parts?.[0]?.text?.trim();
        if (reply) {
          history.push({ role: 'assistant', content: reply });
          conversationMemory.set(senderPhone, history);
          return reply;
        }
      }
    } catch (err) {
      console.error('[!] Gemini API call failed:', err.message);
    }
  }

  // 2. OpenAI
  if (openaiKey) {
    try {
      const model = process.env.OPENAI_MODEL || 'gpt-4o-mini';
      const messages = [{ role: 'system', content: systemPrompt }, ...history];

      const res = await fetch('https://api.openai.com/v1/chat/completions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${openaiKey}`,
        },
        body: JSON.stringify({ model, messages, temperature: 0.3, max_tokens: 250 }),
      });

      if (res.ok) {
        const data = await res.json();
        const reply = data?.choices?.[0]?.message?.content?.trim();
        if (reply) {
          history.push({ role: 'assistant', content: reply });
          conversationMemory.set(senderPhone, history);
          return reply;
        }
      }
    } catch (err) {
      console.error('[!] OpenAI API call failed:', err.message);
    }
  }

  // 3. Built-in contextual fallback
  const fallback =
    'Hi! Thank you for contacting OpsGrids. OpsGrids unifies CRM, attendance, and inventory under one roof. We received your note and will follow up shortly.';
  history.push({ role: 'assistant', content: fallback });
  conversationMemory.set(senderPhone, history);
  return fallback;
}

// ─── Baileys Connection ──────────────────────────────────────────────────────

async function initWhatsAppSocket() {
  if (isStarting) return;
  isStarting = true;

  try {
    if (reconnectTimer) {
      clearTimeout(reconnectTimer);
      reconnectTimer = null;
    }

    if (!fs.existsSync(AUTH_DIR)) {
      fs.mkdirSync(AUTH_DIR, { recursive: true });
    }

    console.log('[*] Fetching latest WhatsApp Web client version...');
    let version = [2, 3000, 1043857760];
    try {
      const latest = await fetchLatestBaileysVersion();
      if (latest?.version) version = latest.version;
    } catch (err) {
      console.warn('[!] Could not fetch latest version, using fallback:', version, err.message);
    }
    console.log('[*] Connecting to WhatsApp Web with version:', version);

    const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);

    sock = makeWASocket({
      version,
      auth: state,
      logger,
      printQRInTerminal: false,
      browser: ['Ubuntu', 'Chrome', '22.04.4'],
      connectTimeoutMs: 60000,
      defaultQueryTimeoutMs: 60000,
    });

    sock.ev.on('creds.update', saveCreds);

    sock.ev.on('connection.update', (update) => {
      const { connection, lastDisconnect, qr } = update;

      if (qr) {
        currentQr = qr;
        connectionStatus = 'waiting_for_scan';
        connectedPhone = null;
        console.log(`[QR] Live WhatsApp QR ready (length=${qr.length})`);
      }

      if (connection === 'close') {
        const statusCode = lastDisconnect?.error?.output?.statusCode;
        const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
        console.log(`[!] WhatsApp socket closed (${statusCode}). Reconnect: ${shouldReconnect}`);

        currentQr = null;
        connectionStatus = 'disconnected';
        connectedPhone = null;

        if (shouldReconnect) {
          reconnectTimer = setTimeout(() => {
            isStarting = false;
            initWhatsAppSocket();
          }, 3000);
        } else {
          console.log('[!] WhatsApp account logged out. Refresh to generate new QR.');
          // Clean credentials on explicit logout
          if (fs.existsSync(AUTH_DIR)) {
            fs.rmSync(AUTH_DIR, { recursive: true, force: true });
          }
          reconnectTimer = setTimeout(() => {
            isStarting = false;
            initWhatsAppSocket();
          }, 2000);
        }
      } else if (connection === 'open') {
        currentQr = null;
        connectionStatus = 'connected';
        const rawId = sock.user?.id || '';
        connectedPhone = rawId.split(':')[0] || rawId.split('@')[0] || rawId;
        console.log(`[+] Personal WhatsApp Connected! Phone: +${connectedPhone}`);
      }
    });

    // Listen for incoming messages
    sock.ev.on('messages.upsert', async (m) => {
      if (m.type !== 'notify') return;

      for (const msg of m.messages) {
        if (msg.key.fromMe) {
          const text =
            msg.message?.conversation || msg.message?.extendedTextMessage?.text || '';
          if (text) {
            const phone = (msg.key.remoteJid || '').replace('@s.whatsapp.net', '');
            pushMessage({
              id: msg.key.id || 'out-' + Date.now(),
              direction: 'outbound',
              phone: '+' + phone,
              senderName: 'You (Personal WhatsApp)',
              message: text,
              timestamp: new Date().toISOString(),
              status: 'delivered',
            });
          }
          continue;
        }

        const remoteJid = msg.key.remoteJid;
        if (!remoteJid || remoteJid.includes('@broadcast') || remoteJid.includes('@g.us')) {
          continue; // Ignore status broadcasts & group messages
        }

        const text =
          msg.message?.conversation || msg.message?.extendedTextMessage?.text || '';
        if (!text || text.trim().length === 0) continue;

        const senderPhone = remoteJid.replace('@s.whatsapp.net', '');
        console.log(`[💬 Inbound] From: +${senderPhone} | "${text}"`);

        pushMessage({
          id: msg.key.id || 'in-' + Date.now(),
          direction: 'inbound',
          phone: '+' + senderPhone,
          senderName: msg.pushName || 'Customer Lead',
          message: text,
          timestamp: new Date().toISOString(),
          status: 'received',
        });

        // Generate AI reply
        try {
          const replyText = await generateAIReply(text, senderPhone);
          console.log(`[📤 Sending Reply] To +${senderPhone}: "${replyText}"`);
          await sock.sendMessage(remoteJid, { text: replyText });

          pushMessage({
            id: 'reply-' + Date.now(),
            direction: 'outbound',
            phone: '+' + senderPhone,
            senderName: 'OpsGrids AI',
            message: replyText,
            timestamp: new Date().toISOString(),
            status: 'ai_replied',
          });
        } catch (err) {
          console.error(`[!] Failed to reply to +${senderPhone}:`, err.message);
        }
      }
    });
  } catch (err) {
    console.error('[!] Failed to initialize WhatsApp socket:', err);
    connectionStatus = 'disconnected';
    reconnectTimer = setTimeout(() => {
      isStarting = false;
      initWhatsAppSocket();
    }, 5000);
  } finally {
    isStarting = false;
  }
}

// ─── HTTP Utilities ──────────────────────────────────────────────────────────

function parseBody(req) {
  return new Promise((resolve) => {
    let data = '';
    req.on('data', (chunk) => {
      data += chunk;
    });
    req.on('end', () => {
      try {
        resolve(data ? JSON.parse(data) : {});
      } catch (_) {
        resolve({});
      }
    });
  });
}

function corsHeaders(req) {
  const origin = req.headers.origin || '*';
  return {
    'Access-Control-Allow-Origin': origin,
    'Access-Control-Allow-Methods': 'GET, POST, OPTIONS',
    'Access-Control-Allow-Headers': 'Content-Type, Authorization',
    'Content-Type': 'application/json',
  };
}

function sendJson(res, req, status, body) {
  res.writeHead(status, corsHeaders(req));
  res.end(JSON.stringify(body));
}

// ─── HTTP Request Router ─────────────────────────────────────────────────────

const server = http.createServer(async (req, res) => {
  if (req.method === 'OPTIONS') {
    res.writeHead(204, corsHeaders(req));
    res.end();
    return;
  }

  const url = new URL(req.url, `http://localhost:${PORT}`);
  const pathname = url.pathname;

  // GET /api/whatsapp-personal/status
  if (pathname === '/api/whatsapp-personal/status' && req.method === 'GET') {
    const hasCreds =
      fs.existsSync(AUTH_DIR) &&
      fs.readdirSync(AUTH_DIR).some((f) => f.startsWith('creds.json'));

    sendJson(res, req, 200, {
      status: connectionStatus,
      phone: connectedPhone ? '+' + connectedPhone.replace(/^\+/, '') : null,
      hasQr: currentQr !== null,
      hasCredentials: hasCreds,
    });
    return;
  }

  // GET /api/whatsapp-personal/qr
  if (pathname === '/api/whatsapp-personal/qr' && req.method === 'GET') {
    if (connectionStatus === 'connected') {
      sendJson(res, req, 200, {
        qr: null,
        status: 'connected',
        phone: connectedPhone ? '+' + connectedPhone.replace(/^\+/, '') : null,
      });
    } else if (currentQr) {
      sendJson(res, req, 200, {
        qr: currentQr,
        status: 'waiting_for_scan',
      });
    } else {
      sendJson(res, req, 200, {
        qr: null,
        status: connectionStatus,
      });
    }
    return;
  }

  // POST /api/whatsapp-personal/pairing-code (for "Link with phone number instead")
  if (pathname === '/api/whatsapp-personal/pairing-code' && req.method === 'POST') {
    const body = await parseBody(req);
    const cleanPhone = String(body.phone || '').replace(/[^0-9]/g, '');

    if (!cleanPhone || cleanPhone.length < 8) {
      sendJson(res, req, 400, {
        error: 'Please enter a valid phone number with country code (e.g. 919019527568).',
      });
      return;
    }

    if (!sock) {
      sendJson(res, req, 503, {
        error: 'WhatsApp engine is initializing. Please try again in a few seconds.',
      });
      return;
    }

    try {
      console.log(`[*] Requesting WhatsApp pairing code for +${cleanPhone}...`);
      const code = await sock.requestPairingCode(cleanPhone);
      console.log(`[+] Pairing code received: ${code}`);
      sendJson(res, req, 200, {
        code,
        phone: '+' + cleanPhone,
        status: 'pairing_code_generated',
      });
    } catch (err) {
      console.error('[!] Failed to generate pairing code:', err);
      sendJson(res, req, 500, {
        error: err.message || 'Failed to request pairing code from WhatsApp',
      });
    }
    return;
  }

  // POST /api/whatsapp-personal/disconnect
  if (pathname === '/api/whatsapp-personal/disconnect' && req.method === 'POST') {
    try {
      if (sock) {
        await sock.logout();
      }
    } catch (_) {}

    if (fs.existsSync(AUTH_DIR)) {
      fs.rmSync(AUTH_DIR, { recursive: true, force: true });
    }

    currentQr = null;
    connectionStatus = 'disconnected';
    connectedPhone = null;

    setTimeout(() => {
      isStarting = false;
      initWhatsAppSocket();
    }, 1000);

    sendJson(res, req, 200, {
      ok: true,
      message: 'Personal WhatsApp disconnected. Fresh pairing token ready.',
    });
    return;
  }

  // GET /api/whatsapp-personal/messages
  if (pathname === '/api/whatsapp-personal/messages' && req.method === 'GET') {
    const limit = parseInt(url.searchParams.get('limit') || '50', 10);
    sendJson(res, req, 200, {
      messages: messageLog.slice(0, limit),
    });
    return;
  }

  // POST /api/whatsapp-personal/simulate
  if (pathname === '/api/whatsapp-personal/simulate' && req.method === 'POST') {
    const body = await parseBody(req);
    const phone = String(body.phone || '+91 98765 43210');
    const msg = String(body.message || 'Hello, I want to learn about OpsGrids.');
    const reply = await generateAIReply(msg, phone.replace(/[^0-9]/g, ''));

    const simulatedInbound = {
      id: 'sim-in-' + Date.now(),
      direction: 'inbound',
      phone,
      senderName: 'Customer Lead',
      message: msg,
      timestamp: new Date().toISOString(),
      status: 'received',
    };
    const simulatedOutbound = {
      id: 'sim-out-' + Date.now(),
      direction: 'outbound',
      phone,
      senderName: 'OpsGrids AI',
      message: reply,
      timestamp: new Date().toISOString(),
      status: 'ai_replied',
    };

    pushMessage(simulatedOutbound);
    pushMessage(simulatedInbound);

    sendJson(res, req, 200, {
      reply,
      inbound: simulatedInbound,
      outbound: simulatedOutbound,
    });
    return;
  }

  // GET /api/whatsapp-personal/conversations
  if (pathname === '/api/whatsapp-personal/conversations' && req.method === 'GET') {
    const status = url.searchParams.get('status') || '';
    const items = buildConversationsList(status);
    sendJson(res, req, 200, {
      items,
      total: items.length,
      page: 1,
      size: 50,
    });
    return;
  }

  // GET /api/whatsapp-personal/conversations/:id
  const convDetailMatch = pathname.match(/^\/api\/whatsapp-personal\/conversations\/([^/]+)$/);
  if (convDetailMatch && req.method === 'GET') {
    const convId = decodeURIComponent(convDetailMatch[1]);
    const items = buildConversationsList();
    const found = items.find((c) => c.id === convId);
    if (found) {
      sendJson(res, req, 200, found);
    } else {
      const digits = convId.replace(/[^0-9]/g, '');
      sendJson(res, req, 200, {
        id: convId,
        wa_address: '+' + digits,
        channel: 'whatsapp',
        status: 'open',
        last_customer_message_at: new Date().toISOString(),
        within_service_window: true,
        contact_id: null,
        assignee_membership_id: null,
        created_at: new Date().toISOString(),
        updated_at: new Date().toISOString(),
      });
    }
    return;
  }

  // GET /api/whatsapp-personal/conversations/:id/messages
  const convMsgsMatch = pathname.match(/^\/api\/whatsapp-personal\/conversations\/([^/]+)\/messages$/);
  if (convMsgsMatch && req.method === 'GET') {
    const convId = decodeURIComponent(convMsgsMatch[1]);
    const digits = convId.replace(/[^0-9]/g, '');
    const thread = messageLog
      .filter((m) => {
        const mDigits = (m.phone || '').replace(/[^0-9]/g, '');
        return mDigits.endsWith(digits) || digits.endsWith(mDigits);
      })
      .map((m) => ({
        id: m.id,
        direction: m.direction,
        message_type: 'text',
        body: m.message,
        occurred_at: m.timestamp,
        provider_message_id: m.id,
        profile_name: m.senderName || null,
      }))
      .sort((a, b) => new Date(a.occurred_at).getTime() - new Date(b.occurred_at).getTime());

    sendJson(res, req, 200, {
      items: thread,
      total: thread.length,
      page: 1,
      size: 100,
    });
    return;
  }

  // POST /api/whatsapp-personal/conversations/:id/reply
  const convReplyMatch = pathname.match(/^\/api\/whatsapp-personal\/conversations\/([^/]+)\/reply$/);
  if (convReplyMatch && req.method === 'POST') {
    const convId = decodeURIComponent(convReplyMatch[1]);
    const digits = convId.replace(/[^0-9]/g, '');
    const body = await parseBody(req);
    const text = String(body.body || body.message || '').trim();

    if (!text) {
      sendJson(res, req, 400, { error: 'Reply text cannot be empty' });
      return;
    }

    let providerMessageId = 'out-' + Date.now();
    let sentLive = false;

    if (sock && connectionStatus === 'connected' && digits) {
      try {
        const jid = `${digits}@s.whatsapp.net`;
        const sent = await sock.sendMessage(jid, { text });
        if (sent?.key?.id) providerMessageId = sent.key.id;
        sentLive = true;
        console.log(`[💬 Outbound Reply] Sent to +${digits}: "${text}"`);
      } catch (err) {
        console.error('[!] Failed to send WhatsApp reply via socket:', err.message);
      }
    }

    const outbound = {
      id: providerMessageId,
      direction: 'outbound',
      phone: '+' + digits,
      senderName: 'You (Personal WhatsApp)',
      message: text,
      timestamp: new Date().toISOString(),
      status: sentLive ? 'delivered' : 'pending',
    };
    pushMessage(outbound);

    sendJson(res, req, 200, {
      message_id: outbound.id,
      state: outbound.status,
      conversation_id: convId,
    });
    return;
  }

  // POST /api/whatsapp-personal/conversations/:id/close
  const convCloseMatch = pathname.match(/^\/api\/whatsapp-personal\/conversations\/([^/]+)\/close$/);
  if (convCloseMatch && req.method === 'POST') {
    const convId = decodeURIComponent(convCloseMatch[1]);
    conversationStatusMap.set(convId, 'closed');
    persistConversationStatus();
    const items = buildConversationsList();
    const found = items.find((c) => c.id === convId);
    sendJson(res, req, 200, found || { id: convId, status: 'closed' });
    return;
  }

  // POST /api/whatsapp-personal/send (Direct outbound send)
  if (pathname === '/api/whatsapp-personal/send' && req.method === 'POST') {
    const body = await parseBody(req);
    const cleanPhone = String(body.phone || '').replace(/[^0-9]/g, '');
    const text = String(body.message || body.text || '').trim();

    if (!cleanPhone || !text) {
      sendJson(res, req, 400, { error: 'Phone and message are required' });
      return;
    }

    let providerMessageId = 'out-' + Date.now();
    let sentLive = false;

    if (sock && connectionStatus === 'connected') {
      try {
        const jid = `${cleanPhone}@s.whatsapp.net`;
        const sent = await sock.sendMessage(jid, { text });
        if (sent?.key?.id) providerMessageId = sent.key.id;
        sentLive = true;
        console.log(`[💬 Direct Send] Sent to +${cleanPhone}: "${text}"`);
      } catch (err) {
        console.error('[!] Failed to send direct WhatsApp message:', err.message);
      }
    }

    const outbound = {
      id: providerMessageId,
      direction: 'outbound',
      phone: '+' + cleanPhone,
      senderName: 'You (Personal WhatsApp)',
      message: text,
      timestamp: new Date().toISOString(),
      status: sentLive ? 'delivered' : 'pending',
    };
    pushMessage(outbound);

    sendJson(res, req, 200, {
      ok: true,
      message: outbound,
      sentLive,
    });
    return;
  }

  // GET /health
  if (pathname === '/health' && req.method === 'GET') {
    sendJson(res, req, 200, {
      ok: true,
      status: connectionStatus,
      connectedPhone,
    });
    return;
  }

  sendJson(res, req, 404, { error: 'Not found' });
});

loadPersistedState();

server.listen(PORT, '0.0.0.0', () => {
  console.log(`[*] WhatsApp Personal Bridge running on http://0.0.0.0:${PORT}`);
  initWhatsAppSocket();
});
