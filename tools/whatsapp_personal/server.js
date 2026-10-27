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
const AI_CONFIG_FILE = path.join(DATA_DIR, 'ai_config.json');
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

// Human operator active takeover tracking (convId -> expiration timestamp)
const operatorActiveMap = new Map();

function markOperatorActive(convId) {
  const takeoverMinutes = parseInt(aiConfig.humanTakeoverMinutes || '30', 10);
  const takeoverMs = Math.max(1, takeoverMinutes) * 60 * 1000;
  operatorActiveMap.set(convId, Date.now() + takeoverMs);
  console.log(`[👤 Operator Active] Human takeover active for ${convId} (${takeoverMinutes} min)`);
}

function isOperatorActive(convId) {
  const expiresAt = operatorActiveMap.get(convId);
  if (!expiresAt) return false;
  if (Date.now() > expiresAt) {
    operatorActiveMap.delete(convId);
    return false;
  }
  return true;
}

// AI Configuration state
let aiConfig = {
  provider: 'auto', // 'auto' | 'gemini' | 'openai'
  geminiKey: process.env.GEMINI_API_KEY || '',
  geminiModel: process.env.GEMINI_MODEL || 'gemini-1.5-flash',
  openaiKey: process.env.OPENAI_API_KEY || '',
  openaiModel: process.env.OPENAI_MODEL || 'gpt-4o-mini',
  businessName: 'OpsGrids',
  businessDescription: 'Unified enterprise software suite for CRM, Attendance, Invoicing, and Inventory.',
  welcomeMessage: 'Hi! Thank you for contacting {businessName}. We received your message and will follow up shortly. How can we help you today?',
  customKnowledge: '',
  autoReplyMode: 'welcome_only', // 'all_inbound' | 'welcome_only' | 'paused'
  humanTakeoverMinutes: 30,
  autoReplyEnabled: true,
};

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

  try {
    if (fs.existsSync(AI_CONFIG_FILE)) {
      const data = fs.readFileSync(AI_CONFIG_FILE, 'utf8');
      const loaded = JSON.parse(data);
      aiConfig = { ...aiConfig, ...loaded };
      if (!aiConfig.geminiKey && process.env.GEMINI_API_KEY) {
        aiConfig.geminiKey = process.env.GEMINI_API_KEY;
      }
      if (!aiConfig.openaiKey && process.env.OPENAI_API_KEY) {
        aiConfig.openaiKey = process.env.OPENAI_API_KEY;
      }
    }
  } catch (err) {
    console.error('[!] Failed to load ai_config.json:', err.message);
  }
}

function persistAiConfig() {
  try {
    if (!fs.existsSync(DATA_DIR)) {
      fs.mkdirSync(DATA_DIR, { recursive: true });
    }
    fs.writeFileSync(AI_CONFIG_FILE, JSON.stringify(aiConfig, null, 2), 'utf8');
  } catch (err) {
    console.error('[!] Failed to persist ai_config.json:', err.message);
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
    const opActive = isOperatorActive(convId);
    const aiPaused = opActive || aiConfig.autoReplyMode === 'paused' || aiConfig.autoReplyEnabled === false;

    if (!existing) {
      convMap.set(convId, {
        id: convId,
        wa_address: m.phone.startsWith('+') ? m.phone : '+' + m.phone,
        remoteJid: m.remoteJid || null,
        channel: 'whatsapp',
        status,
        last_customer_message_at: mTime,
        within_service_window: true,
        contact_id: null,
        assignee_membership_id: null,
        created_at: mTime,
        updated_at: mTime,
        profile_name: m.senderName || null,
        operator_active: opActive,
        ai_paused: aiPaused,
      });
    } else {
      existing.updated_at = mTime;
      existing.last_customer_message_at = mTime;
      existing.operator_active = opActive;
      existing.ai_paused = aiPaused;
      if (m.remoteJid) existing.remoteJid = m.remoteJid;
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

function renderWelcomeMessage(template, businessName) {
  const name = businessName || 'our company';
  const raw =
    template && template.trim().length > 0
      ? template.trim()
      : 'Hi! Thank you for contacting {businessName}. We received your message and our team will be with you shortly. How can we help you today?';
  return raw.replace(/\{businessName\}/gi, name);
}

function hasBeenGreetedRecently(senderPhone, hours = 24) {
  const clean = String(senderPhone || '').replace(/[^0-9]/g, '');
  if (!clean) return false;
  const cutoff = Date.now() - hours * 60 * 60 * 1000;
  return messageLog.some((m) => {
    if (m.direction !== 'outbound') return false;
    const mClean = (m.phone || '').replace(/[^0-9]/g, '');
    if (!mClean.endsWith(clean) && !clean.endsWith(mClean)) return false;
    const time = new Date(m.timestamp).getTime();
    return time > cutoff;
  });
}

function isDuplicateOrRecentOutbound(senderPhone, replyText) {
  if (!replyText) return true;
  const clean = String(senderPhone || '').replace(/[^0-9]/g, '');
  const normReply = replyText.toLowerCase().replace(/[^a-z0-9]/g, '');

  const cutoff = Date.now() - 2 * 60 * 60 * 1000; // last 2 hours
  const recentOutbounds = messageLog
    .filter((m) => {
      if (m.direction !== 'outbound') return false;
      const mClean = (m.phone || '').replace(/[^0-9]/g, '');
      if (!mClean.endsWith(clean) && !clean.endsWith(mClean)) return false;
      const time = new Date(m.timestamp).getTime();
      return time > cutoff;
    })
    .slice(0, 4);

  for (const m of recentOutbounds) {
    const normPrev = (m.message || '').toLowerCase().replace(/[^a-z0-9]/g, '');
    if (normReply === normPrev) {
      return true; // Exact match
    }
    const isReplyWelcome =
      normReply.includes('thankyouforcontacting') ||
      normReply.includes('receivedyourmessage') ||
      normReply.includes('receivedyournote');
    const isPrevWelcome =
      normPrev.includes('thankyouforcontacting') ||
      normPrev.includes('receivedyourmessage') ||
      normPrev.includes('receivedyournote');
    if (isReplyWelcome && isPrevWelcome) {
      return true; // Prevent greeting loop
    }
  }
  return false;
}

function getRecentConversationHistory(senderPhone, limit = 8) {
  const clean = String(senderPhone || '').replace(/[^0-9]/g, '');
  if (!clean) return [];
  const matches = messageLog
    .filter((m) => {
      const mClean = (m.phone || '').replace(/[^0-9]/g, '');
      return mClean.endsWith(clean) || clean.endsWith(mClean);
    })
    .slice(0, limit)
    .reverse();

  return matches.map((m) => ({
    role: m.direction === 'outbound' ? 'assistant' : 'user',
    content: m.message,
  }));
}

function analyzePastConversation(pastHistory) {
  if (!Array.isArray(pastHistory)) return { lastBotMsg: '', userTopics: new Set() };

  let lastBotMsg = '';
  const userTopics = new Set();

  // Traverse in reverse to find the previous assistant message and extract topics from user turns
  for (let i = pastHistory.length - 1; i >= 0; i--) {
    const item = pastHistory[i];
    if (item.role === 'assistant' && !lastBotMsg && item.content) {
      lastBotMsg = item.content;
    }
    if (item.role === 'user' && item.content) {
      const text = item.content.toLowerCase();
      if (text.includes('crm') || text.includes('sales') || text.includes('pipeline') || text.includes('deal')) {
        userTopics.add('crm');
      }
      if (
        text.includes('attendance') ||
        text.includes('hrms') ||
        text.includes('leave') ||
        text.includes('clock') ||
        text.includes('shift')
      ) {
        userTopics.add('attendance');
      }
      if (
        text.includes('invoice') ||
        text.includes('billing') ||
        text.includes('payment') ||
        text.includes('gst')
      ) {
        userTopics.add('invoicing');
      }
      if (
        text.includes('inventory') ||
        text.includes('stock') ||
        text.includes('warehouse')
      ) {
        userTopics.add('inventory');
      }
      if (
        text.includes('demo') ||
        text.includes('walkthrough') ||
        text.includes('trial') ||
        text.includes('schedule')
      ) {
        userTopics.add('demo');
      }
      if (
        text.includes('price') ||
        text.includes('cost') ||
        text.includes('quot') ||
        text.includes('how much')
      ) {
        userTopics.add('pricing');
      }
    }
  }

  return { lastBotMsg, userTopics };
}

function generateSmartFallback(incomingText, pastHistory, senderPhone) {
  const lower = incomingText.toLowerCase().trim();
  const company = aiConfig.businessName || 'OpsGrids';
  const desc = aiConfig.businessDescription || '';
  const isOpsGrids = company.toLowerCase().includes('opsgrids');

  const { lastBotMsg, userTopics } = analyzePastConversation(pastHistory);
  const lastBotLower = (lastBotMsg || '').toLowerCase();

  // 1. Check if user is sharing date, time, availability or email address
  const hasEmail = /\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b/.test(incomingText);
  const hasDateTime =
    /\b(tomorrow|today|tonight|morning|afternoon|evening|monday|tuesday|wednesday|thursday|friday|saturday|sunday|\d{1,2}\s*(am|pm)|\d{1,2}:\d{2})\b/i.test(lower) ||
    lower.includes('this week') ||
    lower.includes('next week') ||
    lower.includes('at 3') ||
    lower.includes('at 4') ||
    lower.includes('at 5') ||
    lower.includes('at 11') ||
    lower.includes('at 12');

  if (hasEmail || (hasDateTime && (lastBotLower.includes('time') || lastBotLower.includes('date') || lastBotLower.includes('schedule') || lastBotLower.includes('walkthrough') || lastBotLower.includes('demo')))) {
    return `Thank you! We've noted your availability for the ${company} walkthrough. Our specialist will confirm and send the calendar invite to your WhatsApp and email. Feel free to ask if you have any questions in the meantime!`;
  }

  // 2. Affirmative / agreement answers ("yes", "sure", "yeah", "yep", "please", "okay", "interested", "ok", "haan", "show me")
  const isAffirmative = /^(yes|yeah|yep|sure|definitely|absolutely|certainly|ok|okay|please|yup|ya|ha|haan|s|1|show me|interested|book it|schedule it)\b/i.test(lower);

  if (isAffirmative) {
    if (
      lastBotLower.includes('walkthrough') ||
      lastBotLower.includes('demo') ||
      lastBotLower.includes('schedule') ||
      lastBotLower.includes('see how it works') ||
      lastBotLower.includes('more details')
    ) {
      if (userTopics.has('crm') || lastBotLower.includes('crm')) {
        return `Awesome! What day, time, and email address work best for your live ${company} CRM walkthrough? We will send the calendar invite right away.`;
      }
      if (userTopics.has('attendance') || lastBotLower.includes('attendance') || lastBotLower.includes('hrms')) {
        return `Awesome! What day, time, and email address work best for your live ${company} HRMS & Attendance walkthrough? We will send the calendar invite right away.`;
      }
      if (userTopics.has('invoicing') || lastBotLower.includes('invoice') || lastBotLower.includes('billing')) {
        return `Awesome! What day, time, and email address work best for your live ${company} Invoicing & Finance walkthrough? We will send the calendar invite right away.`;
      }
      if (userTopics.has('inventory') || lastBotLower.includes('inventory')) {
        return `Awesome! What day, time, and email address work best for your live ${company} Inventory walkthrough? We will send the calendar invite right away.`;
      }
      return `Awesome! What day, time, and email address work best for your live walkthrough with our product specialist?`;
    }

    if (lastBotLower.includes('which solution') || lastBotLower.includes('most interested in')) {
      return `We would be happy to give you a complete walkthrough covering CRM, Attendance, Invoicing, and Inventory. What date and time this week works best for you?`;
    }

    if (lastBotLower.includes('price') || lastBotLower.includes('pricing') || lastBotLower.includes('quote')) {
      return `Great! How many team members will be using ${company}, and which modules do you need? We'll provide an instant pricing estimate.`;
    }

    return `Great! How can our team at ${company} assist you further today?`;
  }

  // 3. User requests / confirms scheduling a demo or walkthrough
  if (
    lower.includes('demo') ||
    lower.includes('walkthrough') ||
    lower.includes('trial') ||
    lower.includes('schedule') ||
    lower.includes('appointment') ||
    lower.includes('consultation') ||
    lower.includes('meeting')
  ) {
    // If the bot ALREADY asked or mentioned scheduling a demo, advance to asking for date/time/email!
    if (lastBotLower.includes('walkthrough') || lastBotLower.includes('demo') || lastBotLower.includes('schedule')) {
      return `Perfect! What date and time this week works best for you? Please also share your email address so our specialist can send the calendar invite.`;
    }
    return `We would be delighted to schedule a demo for ${company}! Could you please share your preferred date/time and email address so our team can coordinate?`;
  }

  // 4. Acknowledgements ("thanks", "thank you", "cool", "got it", "noted")
  if (/^(thx|thanks|thank you|cool|got it|noted|alright|fine)\b/.test(lower)) {
    return `You're welcome! Feel free to ask any other questions about ${company} or let us know if you need anything else.`;
  }

  // 5. Specific Product Inquiries (CRM, Attendance, Invoicing, Inventory)
  if (
    lower.includes('crm') ||
    lower.includes('lead') ||
    lower.includes('sales') ||
    lower.includes('pipeline') ||
    lower.includes('deal') ||
    lower.includes('contact')
  ) {
    if (isOpsGrids) {
      return 'OpsGrids CRM manages your complete sales pipeline, customer contacts, deal stages, and conversion tracking in one unified place. Would you like to see how it works?';
    }
    return `${company} provides customer relationship management and sales tracking. Would you like a demo?`;
  }

  if (
    lower.includes('attendance') ||
    lower.includes('hrms') ||
    lower.includes('leave') ||
    lower.includes('employee') ||
    lower.includes('clock') ||
    lower.includes('shift') ||
    lower.includes('payroll')
  ) {
    if (isOpsGrids) {
      return 'OpsGrids HRMS includes real-time mobile and biometric attendance tracking, shift scheduling, and leave approval workflows. Would you like more details?';
    }
    return `${company} provides employee attendance and workforce management solutions. Would you like more details?`;
  }

  if (
    lower.includes('invoice') ||
    lower.includes('billing') ||
    lower.includes('payment') ||
    lower.includes('gst') ||
    lower.includes('tax') ||
    lower.includes('subscription')
  ) {
    if (isOpsGrids) {
      return 'OpsGrids Finance creates GST-compliant invoices and collects payments via UPI, cards, and net banking with automated reconciliation. Can we help set this up for your team?';
    }
    return `${company} provides invoicing and billing management solutions. Can we share a walkthrough?`;
  }

  if (
    lower.includes('inventory') ||
    lower.includes('stock') ||
    lower.includes('warehouse') ||
    lower.includes('sku') ||
    lower.includes('purchase order') ||
    lower.includes('dispatch')
  ) {
    if (isOpsGrids) {
      return 'OpsGrids Inventory tracks multi-warehouse stock levels, SKU movements, purchase orders, and vendor shipments in real time. Would you like a demo?';
    }
    return `${company} provides stock and inventory tracking. Would you like more details?`;
  }

  if (
    lower.includes('whatsapp') ||
    lower.includes('engage') ||
    lower.includes('campaign') ||
    lower.includes('inbox')
  ) {
    return 'OpsGrids Engage provides 2-way WhatsApp shared inboxes, automated AI auto-replies 24/7, and broadcast marketing campaigns with zero platform dependency.';
  }

  // 6. Products / Services overview
  if (
    lower.includes('product') ||
    lower.includes('software') ||
    lower.includes('feature') ||
    lower.includes('service') ||
    lower.includes('what do you do') ||
    lower.includes('what do you sell') ||
    lower.includes('catalog') ||
    lower.includes('catalogue') ||
    lower.includes('all the products') ||
    lower.includes('walk through me')
  ) {
    if (isOpsGrids) {
      return 'OpsGrids provides unified software for CRM, Attendance & HRMS, Invoicing, Inventory, and WhatsApp customer communication. Which solution are you most interested in?';
    }
    if (desc) {
      return `${company}: ${desc}. Would you like more details or a customized proposal?`;
    }
    return `Thank you for your interest in ${company}! Please let us know which products or services you are looking for.`;
  }

  // 7. Pricing, Cost & Rates
  if (
    lower.includes('price') ||
    lower.includes('pricing') ||
    lower.includes('cost') ||
    lower.includes('quot') ||
    lower.includes('rate') ||
    lower.includes('how much')
  ) {
    if (isOpsGrids) {
      return 'OpsGrids pricing is modular based on the features your business needs (CRM, Attendance, Invoicing, Inventory). Would you like to schedule a 10-minute live walkthrough this week?';
    }
    return `Thank you for inquiring about ${company} pricing. Please let us know your requirements so we can provide a tailored quote.`;
  }

  // 8. Greetings
  if (/^(hi|hello|hey|namaste|good morning|good afternoon|good evening)\b/.test(lower)) {
    const greeted = hasBeenGreetedRecently(senderPhone, 12);
    if (greeted) {
      return `Hello again! How can our team at ${company} help you today?`;
    }
    return renderWelcomeMessage(aiConfig.welcomeMessage, company);
  }

  // 9. Contextual fallback based on previous topics
  if (userTopics.has('crm')) {
    return `Is there anything specific you would like to know about OpsGrids CRM, or would you like to schedule a quick live demo?`;
  }
  if (userTopics.has('attendance')) {
    return `Would you like to schedule a live demo of OpsGrids Attendance & HRMS, or can we send you a feature summary?`;
  }
  if (userTopics.has('demo')) {
    return `What date and time works best for your demo session? Our team will be happy to assist you!`;
  }

  const greeted = hasBeenGreetedRecently(senderPhone, 12);
  if (greeted) {
    return `Thank you for your message! Our team at ${company} has received it and will reply shortly.`;
  }
  return renderWelcomeMessage(aiConfig.welcomeMessage, company);
}

async function generateAIReply(incomingText, senderPhone, isSimulation = false) {
  if (aiConfig.autoReplyMode === 'paused' || aiConfig.autoReplyEnabled === false) {
    console.log(`[⏸️ AI Paused] Auto-replies are paused in settings.`);
    return null;
  }

  const cleanPhone = String(senderPhone || '').replace(/[^0-9]/g, '');
  const convId = 'wa-conv-' + cleanPhone;

  // Check operator active takeover
  if (!isSimulation && isOperatorActive(convId)) {
    console.log(`[⏸️ AI Paused] Human operator is active in ${convId}. Skipping auto-reply.`);
    return null;
  }

  const company = aiConfig.businessName || 'OpsGrids';
  const desc = aiConfig.businessDescription || '';
  const isOpsGrids = company.toLowerCase().includes('opsgrids');

  // Check welcome_only mode
  if (aiConfig.autoReplyMode === 'welcome_only') {
    const greeted = hasBeenGreetedRecently(senderPhone, 24);
    if (greeted) {
      console.log(`[ℹ️ Welcome Only] ${cleanPhone} was already greeted in last 24h. Skipping auto-reply.`);
      return null;
    }
    const welcome = renderWelcomeMessage(aiConfig.welcomeMessage, company);
    return welcome;
  }

  // If in all_inbound mode:
  // First, check anti-repetition: if customer just said "Hi" again and was already greeted in 12h
  const lower = incomingText.toLowerCase().trim();
  const isBareGreeting =
    /^(hi|hello|hey|namaste|good morning|good afternoon|good evening|hola)\b/.test(lower) &&
    lower.split(/\s+/).length <= 3;
  if (isBareGreeting) {
    const greeted = hasBeenGreetedRecently(senderPhone, 12);
    if (greeted) {
      const followUp = `Hello again! How can our team at ${company} help you today?`;
      if (isDuplicateOrRecentOutbound(senderPhone, followUp)) {
        console.log(`[ℹ️ Suppressed] Recent greeting already sent to ${cleanPhone}.`);
        return null;
      }
      return followUp;
    }
  }

  const geminiKey = aiConfig.geminiKey || process.env.GEMINI_API_KEY;
  const openaiKey = aiConfig.openaiKey || process.env.OPENAI_API_KEY;
  const provider = aiConfig.provider || 'auto';

  // Build dialogue history from real conversation messages
  const pastHistory = getRecentConversationHistory(senderPhone, 8);
  pastHistory.push({ role: 'user', content: incomingText });

  const systemPrompt = `You are the helpful, professional customer operations AI assistant for ${company}.
You are replying directly to a customer on WhatsApp.

ABOUT ${company.toUpperCase()}:
- Company Name: ${company}
- Business Profile: ${desc || (isOpsGrids ? 'Enterprise business software suite unifying CRM, HRMS Attendance, GST Invoicing, and Inventory.' : 'Trusted customer-centric business providing quality products and services.')}
${aiConfig.customKnowledge ? `\nCUSTOM PRODUCTS, SERVICES, POLICIES & KNOWLEDGE:\n${aiConfig.customKnowledge}\n` : (isOpsGrids ? `
OPSGRIDS SOFTWARE SUITE:
1. OpsGrids Engage: 2-way WhatsApp inbox, automated AI auto-replies, email broadcasts.
2. OpsGrids CRM: Pipeline tracking, leads, contact management, deal stages.
3. OpsGrids HRMS: Real-time clock-in attendance, shift schedules, leave approvals.
4. OpsGrids Finance: GST invoicing, recurring billing, UPI/card payment collection.
5. OpsGrids Inventory: Multi-warehouse stock tracking, SKU movements, purchase orders.
` : '')}

CRITICAL RULES:
1. Speak ONLY on behalf of ${company}. DO NOT mention other brands or unrelated software.
2. Be conversational, polite, and brief (1 to 3 sentences maximum — this is a WhatsApp message).
3. DO NOT repeat the introductory welcome message if the user is already having an ongoing conversation.
4. Address the customer's specific question or request directly.
5. If the customer asks for a demo, quote, or appointment, invite them to share their preferred time, requirements, or email address.`;

  let reply = null;

  // 1. Google Gemini (if selected or auto)
  if ((provider === 'auto' || provider === 'gemini') && geminiKey) {
    try {
      const model = aiConfig.geminiModel || process.env.GEMINI_MODEL || 'gemini-1.5-flash';
      const url = `https://generativelanguage.googleapis.com/v1beta/models/${model}:generateContent?key=${geminiKey}`;
      const contents = pastHistory.map((h) => ({
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
        reply = data?.candidates?.[0]?.content?.parts?.[0]?.text?.trim() || null;
      } else {
        const errBody = await res.text();
        console.error('[!] Gemini API error:', res.status, errBody);
      }
    } catch (err) {
      console.error('[!] Gemini API call failed:', err.message);
    }
  }

  // 2. OpenAI ChatGPT (if selected or auto fallback)
  if (!reply && (provider === 'auto' || provider === 'openai') && openaiKey) {
    try {
      const model = aiConfig.openaiModel || process.env.OPENAI_MODEL || 'gpt-4o-mini';
      const messages = [{ role: 'system', content: systemPrompt }, ...pastHistory];

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
        reply = data?.choices?.[0]?.message?.content?.trim() || null;
      } else {
        const errBody = await res.text();
        console.error('[!] OpenAI API error:', res.status, errBody);
      }
    } catch (err) {
      console.error('[!] OpenAI API call failed:', err.message);
    }
  }

  // 3. Fallback
  if (!reply) {
    reply = generateSmartFallback(incomingText, pastHistory, senderPhone);
  }

  // 4. Anti-duplication check: if reply matches a recent outbound, select a progressive alternative
  if (reply && isDuplicateOrRecentOutbound(senderPhone, reply)) {
    console.log(`[ℹ️ Duplication Detected] Offering progressive alternative for ${cleanPhone}`);
    const lowerIn = incomingText.toLowerCase();
    if (lowerIn.includes('demo') || lowerIn.includes('walkthrough') || lowerIn.includes('schedule')) {
      reply = `What date and time works best for your live walkthrough? Please share your email address and we'll send the calendar invite.`;
    } else if (lowerIn.includes('price') || lowerIn.includes('cost')) {
      reply = `Our pricing is flexible and modular based on your active modules. How many team members will be using ${company}?`;
    } else if (lowerIn.includes('crm')) {
      reply = `Would you like us to schedule a live CRM screen-share walkthrough or send you a product overview?`;
    } else if (/^(yes|yeah|sure|ok|okay)\b/.test(lowerIn)) {
      reply = `Awesome! Please share your preferred day/time and email address, and our specialist will connect with you.`;
    } else {
      reply = null;
    }

    if (reply && isDuplicateOrRecentOutbound(senderPhone, reply)) {
      console.log(`[ℹ️ Suppressed] Progressive alternative also recently sent to ${cleanPhone}.`);
      return null;
    }
  }

  return reply;
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
        const remoteJid = msg.key.remoteJid || '';
        if (!remoteJid || remoteJid.includes('@broadcast') || remoteJid.includes('@g.us')) {
          continue; // Ignore status broadcasts & group messages
        }

        const rawSender = remoteJid.split('@')[0] || '';
        const displayPhone = rawSender.startsWith('+') ? rawSender : '+' + rawSender;

        if (msg.key.fromMe) {
          const text =
            msg.message?.conversation || msg.message?.extendedTextMessage?.text || '';
          if (text) {
            const cleanDigits = (displayPhone || '').replace(/[^0-9]/g, '');
            if (cleanDigits) {
              markOperatorActive('wa-conv-' + cleanDigits);
            }
            pushMessage({
              id: msg.key.id || 'out-' + Date.now(),
              direction: 'outbound',
              phone: displayPhone,
              remoteJid,
              senderName: 'You (Personal WhatsApp)',
              message: text,
              timestamp: new Date().toISOString(),
              status: 'delivered',
            });
          }
          continue;
        }

        const text =
          msg.message?.conversation || msg.message?.extendedTextMessage?.text || '';
        if (!text || text.trim().length === 0) continue;

        const senderName = msg.pushName || 'Customer Lead';
        console.log(`[💬 Inbound] From: ${displayPhone} (${senderName}) | "${text}"`);

        pushMessage({
          id: msg.key.id || 'in-' + Date.now(),
          direction: 'inbound',
          phone: displayPhone,
          remoteJid,
          senderName,
          message: text,
          timestamp: new Date().toISOString(),
          status: 'received',
        });

        // Generate AI reply
        try {
          const replyText = await generateAIReply(text, rawSender);
          if (replyText && typeof replyText === 'string' && replyText.trim().length > 0) {
            console.log(`[📤 Sending Reply] To ${displayPhone}: "${replyText}"`);
            await sock.sendMessage(remoteJid, { text: replyText });

            pushMessage({
              id: 'reply-' + Date.now(),
              direction: 'outbound',
              phone: displayPhone,
              remoteJid,
              senderName: `${aiConfig.businessName || 'Business'} AI`,
              message: replyText,
              timestamp: new Date().toISOString(),
              status: 'ai_replied',
            });
          }
        } catch (err) {
          console.error(`[!] Failed to reply to ${displayPhone}:`, err.message);
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
    const msg = String(body.message || 'Hello, I want to learn about our services.');
    const reply = await generateAIReply(msg, phone.replace(/[^0-9]/g, ''), true);

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
      senderName: `${aiConfig.businessName || 'Business'} AI`,
      message: reply || '(Auto-reply paused for this inquiry)',
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

    const convItems = buildConversationsList();
    const targetConv = convItems.find((c) => c.id === convId);
    let jid = targetConv?.remoteJid || `${digits}@s.whatsapp.net`;
    if (!jid.includes('@')) jid = `${digits}@s.whatsapp.net`;

    if (sock && connectionStatus === 'connected') {
      try {
        const sent = await sock.sendMessage(jid, { text });
        if (sent?.key?.id) providerMessageId = sent.key.id;
        sentLive = true;
        console.log(`[💬 Outbound Reply] Sent to ${jid}: "${text}"`);
      } catch (err) {
        console.error('[!] Failed to send WhatsApp reply via socket:', err.message);
      }
    }

    const outbound = {
      id: providerMessageId,
      direction: 'outbound',
      phone: '+' + digits,
      remoteJid: jid,
      senderName: 'You (Personal WhatsApp)',
      message: text,
      timestamp: new Date().toISOString(),
      status: sentLive ? 'delivered' : 'pending',
    };
    pushMessage(outbound);

    // Operator manual reply activates human takeover and pauses AI
    markOperatorActive(convId);

    sendJson(res, req, 200, {
      message_id: outbound.id,
      state: outbound.status,
      conversation_id: convId,
    });
    return;
  }

  // POST /api/whatsapp-personal/conversations/:id/resume-ai
  const convResumeMatch = pathname.match(/^\/api\/whatsapp-personal\/conversations\/([^/]+)\/resume-ai$/);
  if (convResumeMatch && req.method === 'POST') {
    const convId = decodeURIComponent(convResumeMatch[1]);
    operatorActiveMap.delete(convId);
    console.log(`[🤖 AI Resumed] Handed ${convId} back to AI auto-reply.`);
    sendJson(res, req, 200, { ok: true, convId, operator_active: false });
    return;
  }

  // POST /api/whatsapp-personal/conversations/:id/pause-ai
  const convPauseMatch = pathname.match(/^\/api\/whatsapp-personal\/conversations\/([^/]+)\/pause-ai$/);
  if (convPauseMatch && req.method === 'POST') {
    const convId = decodeURIComponent(convPauseMatch[1]);
    operatorActiveMap.set(convId, Date.now() + 24 * 60 * 60 * 1000); // 24 hours
    console.log(`[⏸️ AI Paused] Operator paused AI for ${convId}.`);
    sendJson(res, req, 200, { ok: true, convId, operator_active: true });
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

  // GET /api/whatsapp-personal/ai-config
  if (pathname === '/api/whatsapp-personal/ai-config' && req.method === 'GET') {
    const geminiKey = aiConfig.geminiKey || process.env.GEMINI_API_KEY || '';
    const openaiKey = aiConfig.openaiKey || process.env.OPENAI_API_KEY || '';
    sendJson(res, req, 200, {
      provider: aiConfig.provider,
      hasGeminiKey: Boolean(geminiKey),
      geminiKeyMasked: geminiKey
        ? `${geminiKey.slice(0, 6)}...${geminiKey.slice(-4)}`
        : '',
      geminiModel: aiConfig.geminiModel,
      hasOpenaiKey: Boolean(openaiKey),
      openaiKeyMasked: openaiKey
        ? `${openaiKey.slice(0, 4)}...${openaiKey.slice(-4)}`
        : '',
      openaiModel: aiConfig.openaiModel,
      businessName: aiConfig.businessName || 'OpsGrids',
      businessDescription: aiConfig.businessDescription || '',
      welcomeMessage:
        aiConfig.welcomeMessage ||
        'Hi! Thank you for contacting {businessName}. We received your message and will follow up shortly. How can we help you today?',
      customKnowledge: aiConfig.customKnowledge || '',
      autoReplyMode: aiConfig.autoReplyMode || 'welcome_only',
      humanTakeoverMinutes: aiConfig.humanTakeoverMinutes || 30,
      autoReplyEnabled: aiConfig.autoReplyMode !== 'paused',
    });
    return;
  }

  // POST /api/whatsapp-personal/ai-config
  if (pathname === '/api/whatsapp-personal/ai-config' && req.method === 'POST') {
    const body = await parseBody(req);
    if (body.provider !== undefined) aiConfig.provider = body.provider;
    if (typeof body.geminiKey === 'string' && body.geminiKey.trim() !== '') {
      aiConfig.geminiKey = body.geminiKey.trim();
    }
    if (typeof body.geminiModel === 'string' && body.geminiModel.trim() !== '') {
      aiConfig.geminiModel = body.geminiModel.trim();
    }
    if (typeof body.openaiKey === 'string' && body.openaiKey.trim() !== '') {
      aiConfig.openaiKey = body.openaiKey.trim();
    }
    if (typeof body.openaiModel === 'string' && body.openaiModel.trim() !== '') {
      aiConfig.openaiModel = body.openaiModel.trim();
    }
    if (typeof body.businessName === 'string') {
      aiConfig.businessName = body.businessName.trim();
    }
    if (typeof body.businessDescription === 'string') {
      aiConfig.businessDescription = body.businessDescription.trim();
    }
    if (typeof body.welcomeMessage === 'string') {
      aiConfig.welcomeMessage = body.welcomeMessage.trim();
    }
    if (typeof body.customKnowledge === 'string') {
      aiConfig.customKnowledge = body.customKnowledge.trim();
    }
    if (body.autoReplyMode !== undefined) {
      aiConfig.autoReplyMode = body.autoReplyMode;
    }
    if (typeof body.humanTakeoverMinutes === 'number') {
      aiConfig.humanTakeoverMinutes = Math.max(1, body.humanTakeoverMinutes);
    }
    if (body.autoReplyEnabled !== undefined) {
      if (!body.autoReplyEnabled) {
        aiConfig.autoReplyMode = 'paused';
      } else if (aiConfig.autoReplyMode === 'paused') {
        aiConfig.autoReplyMode = 'all_inbound';
      }
      aiConfig.autoReplyEnabled = Boolean(body.autoReplyEnabled);
    }

    persistAiConfig();
    console.log(
      `[+] WhatsApp AI configuration saved: Provider=${aiConfig.provider}, Mode=${aiConfig.autoReplyMode}, GeminiKey=${Boolean(aiConfig.geminiKey)}, OpenAIKey=${Boolean(aiConfig.openaiKey)}, Business="${aiConfig.businessName}"`,
    );

    sendJson(res, req, 200, {
      ok: true,
      message: 'WhatsApp AI settings and client profile updated successfully.',
      hasGeminiKey: Boolean(aiConfig.geminiKey || process.env.GEMINI_API_KEY),
      hasOpenaiKey: Boolean(aiConfig.openaiKey || process.env.OPENAI_API_KEY),
      provider: aiConfig.provider,
      autoReplyMode: aiConfig.autoReplyMode,
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
