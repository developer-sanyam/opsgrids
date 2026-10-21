/**
 * Personal WhatsApp Web Bridge using QR Code Pairing.
 * Connects directly using Multi-Device protocol - NO Meta Business Account required.
 */

import { makeWASocket, useMultiFileAuthState, DisconnectReason } from '@whiskeysockets/baileys';
import qrcode from 'qrcode-terminal';
import pino from 'pino';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';
import dotenv from 'dotenv';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

// Load root .env
dotenv.config({ path: path.resolve(__dirname, '../../.env') });

const AUTH_DIR = path.resolve(__dirname, 'auth_info_baileys');
const logger = pino({ level: 'silent' });

// In-memory conversation context per phone number
const conversationMemory = new Map();

/**
 * Generates an AI reply using either OpenAI ChatGPT or Google Gemini based on environment keys.
 */
async function generateAIReply(incomingText, senderPhone) {
  const geminiKey = process.env.GEMINI_API_KEY;
  const openaiKey = process.env.OPENAI_API_KEY;

  const history = conversationMemory.get(senderPhone) || [];

  // Update memory with latest user message
  history.push({ role: 'user', content: incomingText });
  if (history.length > 10) history.shift(); // keep last 10 messages

  const systemPrompt = `You are a helpful customer operations AI assistant for OpsGrids.
You are replying to a customer on WhatsApp.
Keep your response short, conversational, polite, and concise (1 to 2 sentences).
Do not use markdown headers or lengthy corporate email templates.
OpsGrids helps companies unify CRM, Attendance, Invoicing, and Inventory on one platform.`;

  // Try Google Gemini
  if (geminiKey) {
    try {
      const model = process.env.GEMINI_MODEL || 'gemini-1.5-flash';
      const url = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${geminiKey}`;
      const contents = history.map(h => ({
        role: h.role === 'assistant' ? 'model' : 'user',
        parts: [{ text: h.content }]
      }));

      const res = await fetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          system_instruction: { parts: [{ text: systemPrompt }] },
          contents: contents,
          generationConfig: { temperature: 0.3, maxOutputTokens: 250 }
        })
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

  // Try OpenAI
  if (openaiKey) {
    try {
      const model = process.env.OPENAI_MODEL || 'gpt-4o-mini';
      const messages = [{ role: 'system', content: systemPrompt }, ...history];

      const res = await fetch('https://api.openai.com/v1/chat/completions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${openaiKey}`
        },
        body: JSON.stringify({
          model: model,
          messages: messages,
          temperature: 0.3,
          max_tokens: 250
        })
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

  // Fallback response when no AI key configured
  const fallback = "Hi! Thank you for contacting OpsGrids. We received your message and will get back to you shortly.";
  history.push({ role: 'assistant', content: fallback });
  conversationMemory.set(senderPhone, history);
  return fallback;
}

/**
 * Main WhatsApp Web connection loop.
 */
async function connectToWhatsApp() {
  const { state, saveCreds } = await useMultiFileAuthState(AUTH_DIR);

  const sock = makeWASocket({
    auth: state,
    logger: logger,
    printQRInTerminal: false,
    browser: ['OpsGrids Personal AI', 'Chrome', '1.0.0']
  });

  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', (update) => {
    const { connection, lastDisconnect, qr } = update;

    if (qr) {
      console.log('\n======================================================');
      console.log('   SCAN THIS QR CODE WITH YOUR PERSONAL WHATSAPP    ');
      console.log('   (WhatsApp -> Linked Devices -> Link a Device)      ');
      console.log('======================================================\n');
      qrcode.generate(qr, { small: true });
      console.log('\nWaiting for scan...');
    }

    if (connection === 'close') {
      const statusCode = lastDisconnect?.error?.output?.statusCode;
      const shouldReconnect = statusCode !== DisconnectReason.loggedOut;
      console.log(`[!] WhatsApp connection closed (${statusCode}). Reconnecting: ${shouldReconnect}`);
      if (shouldReconnect) {
        setTimeout(connectToWhatsApp, 3000);
      } else {
        console.log('[!] Logged out. Remove auth_info_baileys directory to scan a new QR code.');
      }
    } else if (connection === 'open') {
      console.log('\n[+] ====================================================');
      console.log(`[+] Personal WhatsApp Connected Successfully!`);
      console.log(`[+] Logged in as: ${sock.user?.id || 'Personal Phone'}`);
      console.log(`[+] Autonomous AI Auto-Responder Active`);
      console.log('[+] ====================================================\n');
    }
  });

  // Listen for incoming messages
  sock.ev.on('messages.upsert', async (m) => {
    if (m.type !== 'notify') return;

    for (const msg of m.messages) {
      // Ignore messages sent by ourselves or status broadcasts
      if (msg.key.fromMe) continue;
      const remoteJid = msg.key.remoteJid;
      if (!remoteJid || remoteJid.includes('@broadcast') || remoteJid.includes('@g.us')) continue; // Skip groups & status

      const text =
        msg.message?.conversation ||
        msg.message?.extendedTextMessage?.text ||
        '';

      if (!text || text.trim().length === 0) continue;

      const senderPhone = remoteJid.replace('@s.whatsapp.net', '');
      console.log(`\n[💬 Inbound WhatsApp] From: +${senderPhone} | Message: "${text}"`);

      // Check anti-loop / rate limiting: avoid self-reply or duplicate loops
      try {
        console.log(`[🤖 AI Thinking...] Generating contextual reply for +${senderPhone}...`);
        const replyText = await generateAIReply(text, senderPhone);

        console.log(`[📤 Sending Reply] To +${senderPhone}: "${replyText}"`);
        await sock.sendMessage(remoteJid, { text: replyText });
        console.log(`[✓ Sent] Reply delivered to +${senderPhone}`);
      } catch (err) {
        console.error(`[!] Failed to send WhatsApp reply to +${senderPhone}:`, err.message);
      }
    }
  });
}

// Check arguments
const args = process.argv.slice(2);
if (args.includes('--status')) {
  if (fs.existsSync(AUTH_DIR) && fs.readdirSync(AUTH_DIR).length > 0) {
    console.log('[+] Personal WhatsApp status: LINKED / CREDENTIALS SAVED');
  } else {
    console.log('[-] Personal WhatsApp status: NOT LINKED (Run connect to scan QR code)');
  }
} else {
  console.log('[*] Initializing Personal WhatsApp Web Bridge...');
  connectToWhatsApp().catch(err => console.error('[!] Fatal error:', err));
}
