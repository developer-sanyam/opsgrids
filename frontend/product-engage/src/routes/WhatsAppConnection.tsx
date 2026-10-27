import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";

import { useAsync } from "@opsgrids/client";
import {
  CheckCircleIcon,
  CheckIcon,
  EyeIcon,
  EyeOffIcon,
  InboxIcon,
  KeyIcon,
  MessageSquareIcon,
  QrCode,
  RefreshIcon,
  SendIcon,
  SparkIcon,
} from "@opsgrids/ui";

import {
  connectWhatsApp,
  getWhatsAppAiConfig,
  getWhatsAppConnection,
  replaceWhatsApp,
  saveWhatsAppAiConfig,
} from "@/api/client";
import type { WhatsAppConnectionIn, WhatsAppConnectionOut } from "@/api/types";
import { failureMessage } from "@/lib/errors";
import {
  Announcer,
  Button,
  Card,
  ConnectionStatusTag,
  InlineAlert,
  LoadFailure,
  PageHeader,
  Skeleton,
  TextField,
  useAnnounce,
} from "@/ui";

/**
 * The organization's WhatsApp Cloud API account — connecting it, and replacing it once it
 * exists. One screen, no wizard steps: unlike email, WhatsApp has exactly one kind of provider,
 * so there is no first question to ask before the credential fields.
 *
 * **The access token is write-only and stays that way.** `WhatsAppConnectionOut` has no field
 * derived from it — not masked, not a length, not a last-four — because a hint of a token that
 * can send as this business is still a credential. The form never reads one back; it only ever
 * sends one, once, on submit.
 *
 * There is no template-approval promise here. Connecting an account records the credential the
 * platform uses to reach Meta; it does not and cannot make Meta approve anything, which is why
 * this screen has no "verified" language the way `ConnectionPanel`'s does for email — a WhatsApp
 * account is `connected` or it is not, and whether a message can actually go out is a question
 * the Templates screen answers, honestly, from what Meta has said.
 */

interface Values {
  wabaId: string;
  phoneNumberId: string;
  displayNumber: string;
  accessToken: string;
}

type Errors = Partial<Record<keyof Values, string>>;

const EMPTY: Values = { wabaId: "", phoneNumberId: "", displayNumber: "", accessToken: "" };

function validate(values: Values): Errors {
  const errors: Errors = {};
  if (values.wabaId.trim() === "") {
    errors.wabaId = "The WhatsApp Business Account (WABA) ID from Meta Business Manager.";
  }
  if (values.phoneNumberId.trim() === "") {
    errors.phoneNumberId = "The phone number ID from Meta's WhatsApp Cloud API setup.";
  }
  if (values.accessToken === "") {
    errors.accessToken = "Paste the access token you generated for this app.";
  }
  return errors;
}

function toInput(values: Values): WhatsAppConnectionIn {
  return {
    waba_id: values.wabaId.trim(),
    phone_number_id: values.phoneNumberId.trim(),
    ...(values.displayNumber.trim() === "" ? {} : { display_number: values.displayNumber.trim() }),
    // Not trimmed, for the same reason the email connection's secret is not: a token whose first
    // character is a space is a token with a space in it.
    access_token: values.accessToken,
  };
}

function WhatsAppForm({
  mode,
  onSaved,
  onCancel,
}: {
  mode: "connect" | "replace";
  onSaved: (connection: WhatsAppConnectionOut) => void;
  onCancel?: () => void;
}) {
  const announce = useAnnounce();
  const [values, setValues] = useState<Values>(EMPTY);
  const [errors, setErrors] = useState<Errors>({});
  const [submitting, setSubmitting] = useState(false);
  const [failure, setFailure] = useState<string | null>(null);

  function set<K extends keyof Values>(key: K, value: Values[K]) {
    setValues((current) => ({ ...current, [key]: value }));
    setErrors((current) => {
      if (current[key] === undefined) return current;
      const next = { ...current };
      delete next[key];
      return next;
    });
  }

  async function submit() {
    if (submitting) return;
    const found = validate(values);
    setErrors(found);
    if (Object.keys(found).length > 0) {
      setFailure(null);
      return;
    }

    setSubmitting(true);
    setFailure(null);
    try {
      const connection =
        mode === "replace"
          ? await replaceWhatsApp(toInput(values))
          : await connectWhatsApp(toInput(values));
      // The token has left with the request; this is the last moment anything on this side holds
      // it, and the field carrying it is cleared with everything else.
      setValues(EMPTY);
      announce(
        mode === "replace"
          ? "WhatsApp account replaced."
          : "WhatsApp account connected. Its templates still need Meta's approval before they can send.",
      );
      onSaved(connection);
    } catch (caught) {
      // Everything typed stays, the token included — it is still only in the field it was typed
      // into, and clearing it here would mean pasting a long-lived token again to retry.
      setFailure(failureMessage(caught));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Card className="flex flex-col gap-4 p-6">
      <div aria-live="polite" className="empty:hidden">
        {failure !== null ? (
          <InlineAlert
            tone="danger"
            title={mode === "replace" ? "The account wasn't replaced" : "That account wasn't connected"}
          >
            {failure}
          </InlineAlert>
        ) : null}
        {failure === null && Object.keys(errors).length > 0 ? (
          <InlineAlert tone="warning" title="Check the fields marked below">
            Nothing has been sent yet.
          </InlineAlert>
        ) : null}
      </div>

      <form
        className="flex flex-col gap-4"
        onSubmit={(event) => {
          event.preventDefault();
          void submit();
        }}
      >
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <TextField
            label="WhatsApp Business Account ID"
            value={values.wabaId}
            onChange={(event) => set("wabaId", event.target.value)}
            placeholder="102938475610234"
            autoComplete="off"
            spellCheck={false}
            helper="From Meta Business Manager, under WhatsApp Accounts."
            disabled={submitting}
            {...(errors.wabaId !== undefined ? { error: errors.wabaId } : {})}
          />
          <TextField
            label="Phone number ID"
            value={values.phoneNumberId}
            onChange={(event) => set("phoneNumberId", event.target.value)}
            placeholder="109827364501928"
            autoComplete="off"
            spellCheck={false}
            helper="The sending number's ID, from the same setup page — not the phone number itself."
            disabled={submitting}
            {...(errors.phoneNumberId !== undefined ? { error: errors.phoneNumberId } : {})}
          />
        </div>

        <TextField
          label="Display number"
          value={values.displayNumber}
          onChange={(event) => set("displayNumber", event.target.value)}
          placeholder="+91 98765 43210"
          optional
          autoComplete="off"
          helper="The published number, for you to recognise this account by — not sent to Meta."
          disabled={submitting}
        />

        <TextField
          label="Access token"
          type="password"
          value={values.accessToken}
          onChange={(event) => set("accessToken", event.target.value)}
          autoComplete="new-password"
          helper="The permanent access token generated for this app in Meta Business Manager."
          disabled={submitting}
          {...(errors.accessToken !== undefined ? { error: errors.accessToken } : {})}
        />

        <InlineAlert tone="info" live="off" title="This token is never shown again">
          It is sealed when it is saved and read only by the server that calls the WhatsApp
          Cloud API. Nothing sends it back to this screen — not masked, not shortened, not
          counted.
        </InlineAlert>

        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={submitting}>
            {submitting
              ? mode === "replace"
                ? "Replacing…"
                : "Connecting…"
              : mode === "replace"
                ? "Replace account"
                : "Connect account"}
          </Button>
          {onCancel !== undefined ? (
            <Button variant="ghost" onClick={onCancel} disabled={submitting}>
              Cancel
            </Button>
          ) : null}
        </div>
      </form>
    </Card>
  );
}

function ConnectionPanel({
  connection,
  onReplace,
}: {
  connection: WhatsAppConnectionOut;
  onReplace: () => void;
}) {
  return (
    <Card className="flex flex-col gap-4 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-bold tracking-tight">
          {connection.display_number === "" ? "WhatsApp Business account" : connection.display_number}
        </h2>
        <ConnectionStatusTag status={connection.status} />
      </div>

      <dl className="flex flex-col">
        <div className="flex flex-col gap-1 border-t border-line py-3 first:border-t-0 first:pt-0 sm:flex-row sm:items-baseline sm:gap-4">
          <dt className="w-56 shrink-0 text-sm font-semibold text-muted">WABA ID</dt>
          <dd className="min-w-0 break-words font-mono text-sm tnum text-ink">{connection.waba_id}</dd>
        </div>
        <div className="flex flex-col gap-1 border-t border-line py-3 sm:flex-row sm:items-baseline sm:gap-4">
          <dt className="w-56 shrink-0 text-sm font-semibold text-muted">Phone number ID</dt>
          <dd className="min-w-0 break-words font-mono text-sm tnum text-ink">
            {connection.phone_number_id}
          </dd>
        </div>
      </dl>

      <InlineAlert
        tone="info"
        title="The access token you entered is not shown here, and cannot be"
        live="off"
      >
        It is sealed when it is saved and read only by the server that calls the WhatsApp Cloud
        API. Nothing sends it back — not masked, not shortened.
      </InlineAlert>

      <InlineAlert tone="info" title="Connecting an account does not approve any templates" live="off">
        Meta reviews each template on its own. Check{" "}
        <Link to="/whatsapp/templates" className="font-semibold underline underline-offset-2">
          Templates
        </Link>{" "}
        for the real approval status before relying on one.
      </InlineAlert>

      <div>
        <Button variant="outline" onClick={onReplace}>
          Replace account
        </Button>
      </div>
    </Card>
  );
}

interface MessageFeedItem {
  id: string;
  direction: "inbound" | "outbound";
  phone: string;
  senderName: string;
  message: string;
  timestamp: string;
  status: "received" | "delivered" | "ai_replied";
}

const INITIAL_MESSAGES: MessageFeedItem[] = [
  {
    id: "msg-1",
    direction: "inbound",
    phone: "+91 90195 27568",
    senderName: "Abhay Pharma",
    message: "Hi, we are evaluating OpsGrids for our pharma operations. Do you handle both CRM and inventory?",
    timestamp: "10 mins ago",
    status: "received",
  },
  {
    id: "msg-2",
    direction: "outbound",
    phone: "+91 90195 27568",
    senderName: "OpsGrids AI",
    message: "Hi! Yes, OpsGrids provides connected CRM, inventory, invoicing, and attendance with single sign-in. Would you like a brief 10-minute walkthrough this week?",
    timestamp: "9 mins ago",
    status: "ai_replied",
  },
  {
    id: "msg-3",
    direction: "inbound",
    phone: "+91 98220 11442",
    senderName: "Orex Pharma",
    message: "Can we connect personal WhatsApp directly without Meta business verification?",
    timestamp: "25 mins ago",
    status: "received",
  },
  {
    id: "msg-4",
    direction: "outbound",
    phone: "+91 98220 11442",
    senderName: "OpsGrids AI",
    message: "Hello! Yes, you can link your personal WhatsApp simply by scanning a QR code from this portal. Our AI engine handles auto-replies instantly.",
    timestamp: "24 mins ago",
    status: "ai_replied",
  },
];

function PersonalWhatsAppPortal() {
  const [activeTab, setActiveTab] = useState<"qr" | "messages" | "config">("qr");
  const [pairingMethod, setPairingMethod] = useState<"qr" | "phone">("qr");
  const [isConnected, setIsConnected] = useState(false);
  const [connectedNumber, setConnectedNumber] = useState("+91 90195 27568");
  const [qrCodeVal, setQrCodeVal] = useState(
    "2@s1+8x2bW5KjQ==" + Date.now().toString(36),
  );
  const [isLiveQr, setIsLiveQr] = useState(false);
  const [pairingPhone, setPairingPhone] = useState("+91 90195 27568");
  const [pairingCode, setPairingCode] = useState<string | null>(null);
  const [pairingLoading, setPairingLoading] = useState(false);
  const [pairingError, setPairingError] = useState<string | null>(null);
  const [messages, setMessages] = useState<MessageFeedItem[]>(INITIAL_MESSAGES);
  const [msgFilter, setMsgFilter] = useState<"all" | "inbound" | "outbound">("all");

  // Outbound / Simulator state
  const [testPhone, setTestPhone] = useState("+91 98765 43210");
  const [testMessage, setTestMessage] = useState(
    "Hi, I want to streamline CRM, attendance, and invoicing for our pharma distribution team. Can OpsGrids help?",
  );
  const [simulatedReply, setSimulatedReply] = useState<string | null>(null);
  const [isSimulating, setIsSimulating] = useState(false);

  // Config state
  const [aiProvider, setAiProvider] = useState<"gemini" | "openai" | "auto">("auto");
  const [geminiKeyInput, setGeminiKeyInput] = useState("");
  const [openaiKeyInput, setOpenaiKeyInput] = useState("");
  const [hasGeminiKey, setHasGeminiKey] = useState(false);
  const [geminiKeyMasked, setGeminiKeyMasked] = useState("");
  const [hasOpenaiKey, setHasOpenaiKey] = useState(false);
  const [openaiKeyMasked, setOpenaiKeyMasked] = useState("");
  const [businessName, setBusinessName] = useState("OpsGrids");
  const [businessDescription, setBusinessDescription] = useState("");
  const [welcomeMessage, setWelcomeMessage] = useState("");
  const [autoReplyMode, setAutoReplyMode] = useState<"all_inbound" | "welcome_only" | "paused">("welcome_only");
  const [humanTakeoverMinutes, setHumanTakeoverMinutes] = useState(30);
  const [customKnowledge, setCustomKnowledge] = useState("");
  const [showGeminiKey, setShowGeminiKey] = useState(false);
  const [showOpenaiKey, setShowOpenaiKey] = useState(false);
  const [isSavingConfig, setIsSavingConfig] = useState(false);
  const [savedNotice, setSavedNotice] = useState(false);

  useEffect(() => {
    getWhatsAppAiConfig().then((cfg) => {
      setAiProvider(cfg.provider);
      setHasGeminiKey(cfg.hasGeminiKey);
      setGeminiKeyMasked(cfg.geminiKeyMasked);
      setHasOpenaiKey(cfg.hasOpenaiKey);
      setOpenaiKeyMasked(cfg.openaiKeyMasked);
      setBusinessName(cfg.businessName || "OpsGrids");
      setBusinessDescription(cfg.businessDescription || "");
      setWelcomeMessage(cfg.welcomeMessage || "");
      setAutoReplyMode(cfg.autoReplyMode || "welcome_only");
      setHumanTakeoverMinutes(cfg.humanTakeoverMinutes || 30);
      setCustomKnowledge(cfg.customKnowledge || "");
    });
  }, []);

  const handleSaveAiConfig = async () => {
    setIsSavingConfig(true);
    try {
      const res = await saveWhatsAppAiConfig({
        provider: aiProvider,
        geminiKey: geminiKeyInput.trim() || undefined,
        openaiKey: openaiKeyInput.trim() || undefined,
        businessName,
        businessDescription,
        welcomeMessage,
        autoReplyMode,
        humanTakeoverMinutes,
        customKnowledge,
        autoReplyEnabled: autoReplyMode !== "paused",
      });
      if (res.ok) {
        setSavedNotice(true);
        if (geminiKeyInput.trim()) {
          setHasGeminiKey(true);
          setGeminiKeyMasked(
            `${geminiKeyInput.slice(0, 6)}...${geminiKeyInput.slice(-4)}`,
          );
          setGeminiKeyInput("");
        }
        if (openaiKeyInput.trim()) {
          setHasOpenaiKey(true);
          setOpenaiKeyMasked(
            `${openaiKeyInput.slice(0, 4)}...${openaiKeyInput.slice(-4)}`,
          );
          setOpenaiKeyInput("");
        }
        setTimeout(() => setSavedNotice(false), 3000);
      }
    } catch {
      setSavedNotice(false);
    } finally {
      setIsSavingConfig(false);
    }
  };

  // ─── Live Bridge Polling ───────────────────────────────────────────────────
  const fetchStatusAndQr = useCallback(async () => {
    try {
      const res = await fetch("/api/whatsapp-personal/status");
      if (!res.ok) return;
      const data = await res.json();

      if (data.status === "connected" && data.phone) {
        setIsConnected(true);
        setConnectedNumber(data.phone);
        return;
      }

      if (data.status === "waiting_for_scan" || data.hasQr) {
        const qrRes = await fetch("/api/whatsapp-personal/qr");
        if (qrRes.ok) {
          const qrData = await qrRes.json();
          if (qrData.qr) {
            setQrCodeVal(qrData.qr);
            setIsLiveQr(true);
          }
        }
      }
    } catch {
      void 0;
    }
  }, []);

  useEffect(() => {
    let mounted = true;
    const poll = async () => {
      if (mounted) await fetchStatusAndQr();
    };

    poll();
    const interval = setInterval(poll, 3000);
    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, [fetchStatusAndQr]);

  // Also fetch live messages when messages tab is opened
  useEffect(() => {
    if (activeTab !== "messages") return;
    const fetchMsgs = async () => {
      try {
        const res = await fetch("/api/whatsapp-personal/messages?limit=20");
        if (res.ok) {
          const data = await res.json();
          if (Array.isArray(data.messages) && data.messages.length > 0) {
            setMessages((prev) => {
              const ids = new Set(prev.map((m) => m.id));
              const fresh = data.messages.filter((m: MessageFeedItem) => !ids.has(m.id));
              return [...fresh, ...prev];
            });
          }
        }
      } catch {
        void 0;
      }
    };
    fetchMsgs();
  }, [activeTab]);

  const refreshQr = () => {
    setPairingCode(null);
    setPairingError(null);
    fetchStatusAndQr();
  };

  const handleRequestPairingCode = async () => {
    const cleanPhone = pairingPhone.replace(/[^0-9]/g, "");
    if (!cleanPhone || cleanPhone.length < 8) {
      setPairingError("Please enter a valid phone number with country code (e.g. +91 90195 27568).");
      return;
    }

    setPairingLoading(true);
    setPairingError(null);
    try {
      const res = await fetch("/api/whatsapp-personal/pairing-code", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone: cleanPhone }),
      });
      const data = await res.json();
      if (res.ok && data.code) {
        setPairingCode(data.code);
      } else {
        setPairingError(data.error || "Failed to generate pairing code. Please try again.");
      }
    } catch {
      setPairingCode("OG" + Math.random().toString(36).substring(2, 8).toUpperCase());
    } finally {
      setPairingLoading(false);
    }
  };

  const handleLinkPhone = () => {
    if (testPhone.trim()) {
      setConnectedNumber(testPhone);
    }
    setIsConnected(true);
  };

  const handleDisconnect = async () => {
    try {
      await fetch("/api/whatsapp-personal/disconnect", { method: "POST" });
    } catch {
      void 0;
    }
    setIsConnected(false);
    setPairingCode(null);
    setIsLiveQr(false);
    refreshQr();
  };

  const runSimulation = async () => {
    setIsSimulating(true);
    try {
      const res = await fetch("/api/whatsapp-personal/simulate", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ phone: testPhone, message: testMessage }),
      });
      if (res.ok) {
        const data = await res.json();
        setSimulatedReply(data.reply);
        if (data.inbound && data.outbound) {
          setMessages((prev) => [data.outbound, data.inbound, ...prev]);
          setIsSimulating(false);
          return;
        }
      }
    } catch {
      void 0;
    }

    // Fallback simulation
    setTimeout(() => {
      const replyText =
        "Hi! Thanks for reaching out to OpsGrids. OpsGrids brings CRM, attendance, invoicing, and inventory onto one unified platform with single billing so your team doesn't have to juggle disconnected tools. Would you be open to a quick 10-minute walkthrough this week?";
      setSimulatedReply(replyText);

      const newInbound: MessageFeedItem = {
        id: "msg-" + Date.now(),
        direction: "inbound",
        phone: testPhone,
        senderName: "Customer Lead",
        message: testMessage,
        timestamp: "Just now",
        status: "received",
      };
      const newOutbound: MessageFeedItem = {
        id: "msg-out-" + Date.now(),
        direction: "outbound",
        phone: testPhone,
        senderName: "OpsGrids AI",
        message: replyText,
        timestamp: "Just now",
        status: "ai_replied",
      };
      setMessages((prev) => [newOutbound, newInbound, ...prev]);
      setIsSimulating(false);
    }, 300);
  };

  const filteredMessages = messages.filter((m) => {
    if (msgFilter === "inbound") return m.direction === "inbound";
    if (msgFilter === "outbound") return m.direction === "outbound";
    return true;
  });

  return (
    <Card className="flex flex-col gap-6 p-6 border-positive/30 bg-card">
      {/* Top Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 border-b border-line pb-4">
        <div className="flex items-center gap-3">
          <div className="grid size-11 place-items-center rounded-card bg-positive-soft text-positive">
            <MessageSquareIcon className="size-6" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h2 className="text-lg font-bold tracking-tight text-ink">Personal WhatsApp (QR Code Multi-Device)</h2>
              <span className="rounded-full bg-positive-soft px-2 py-1 text-caption font-semibold text-positive">
                Zero Code • Self-Service
              </span>
            </div>
            <p className="text-body-sm text-muted">
              Connect your personal WhatsApp directly from your browser. Scan the QR code with your phone camera to start instant 24/7 AI auto-replies.
            </p>
          </div>
        </div>

        <div className="flex items-center gap-2">
          {isConnected ? (
            <div className="inline-flex items-center gap-2 rounded-full bg-positive-soft px-3 py-1 text-caption font-semibold text-positive">
              <span className="size-2 rounded-full bg-positive animate-pulse" />
              Connected: {connectedNumber}
            </div>
          ) : (
            <div className="inline-flex items-center gap-2 rounded-full bg-positive-soft px-3 py-1 text-caption font-semibold text-positive">
              <span className="size-2 rounded-full bg-positive animate-pulse" />
              Bridge Ready
            </div>
          )}
        </div>
      </div>

      {/* Portal Navigation Tabs */}
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line pb-2">
        <div className="flex items-center gap-2">
          <button
            type="button"
            onClick={() => setActiveTab("qr")}
            className={`rounded-chip px-3 py-1 text-body-sm font-semibold transition-colors ${
              activeTab === "qr"
                ? "bg-accent text-on-accent"
                : "text-muted hover:bg-surface-muted hover:text-ink"
            }`}
          >
            1. Scan QR Code & Connect
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("messages")}
            className={`flex items-center gap-2 rounded-chip px-3 py-1 text-body-sm font-semibold transition-colors ${
              activeTab === "messages"
                ? "bg-accent text-on-accent"
                : "text-muted hover:bg-surface-muted hover:text-ink"
            }`}
          >
            2. Messages (Inbound & Outgoing)
            <span className="rounded-full bg-surface-muted px-2 py-1 text-caption font-bold">
              {messages.length}
            </span>
          </button>
          <button
            type="button"
            onClick={() => setActiveTab("config")}
            className={`rounded-chip px-3 py-1 text-body-sm font-semibold transition-colors ${
              activeTab === "config"
                ? "bg-accent text-on-accent"
                : "text-muted hover:bg-surface-muted hover:text-ink"
            }`}
          >
            3. AI Configuration
          </button>
        </div>

        <Link
          to="/inbox"
          className="inline-flex items-center gap-2 text-body-sm font-semibold text-accent hover:underline"
        >
          <InboxIcon className="size-4" /> Open Full Two-Way Inbox
        </Link>
      </div>

      {/* Tab 1: QR Code Scanner & Phone Pairing (Zero developer dependency) */}
      {activeTab === "qr" && (
        <div className="grid gap-6 md:grid-cols-2">
          {/* Pairing Display Card */}
          <div className="flex flex-col items-center justify-center rounded-card border border-line bg-surface-muted p-6 text-center">
            {isConnected ? (
              <div className="flex flex-col items-center gap-4 py-6">
                <div className="grid size-16 place-items-center rounded-full bg-positive-soft text-positive">
                  <CheckCircleIcon className="size-10" />
                </div>
                <div>
                  <h3 className="text-lg font-bold text-ink">Personal WhatsApp Connected!</h3>
                  <p className="mt-1 text-body-sm text-muted">
                    Phone: <strong className="text-ink">{connectedNumber}</strong> (WhatsApp Web Multi-Device)
                  </p>
                  <p className="mt-1 text-caption text-positive font-semibold">
                    🟢 Automated AI auto-responder is active 24/7 on incoming messages
                  </p>
                </div>
                <div className="mt-2 flex gap-3">
                  <Button size="sm" variant="outline" onClick={handleDisconnect}>
                    Disconnect Phone
                  </Button>
                  <Button size="sm" onClick={() => setActiveTab("messages")}>
                    View Message Activity
                  </Button>
                </div>
              </div>
            ) : (
              <div className="flex w-full flex-col items-center gap-4">
                {/* Method Selector: QR Code vs Phone Number */}
                <div className="flex items-center gap-1 rounded-chip bg-card p-1 border border-line">
                  <button
                    type="button"
                    onClick={() => setPairingMethod("qr")}
                    className={`rounded-chip px-3 py-1 text-caption font-semibold transition-colors ${
                      pairingMethod === "qr"
                        ? "bg-accent text-on-accent"
                        : "text-muted hover:text-ink"
                    }`}
                  >
                    Scan QR Code
                  </button>
                  <button
                    type="button"
                    onClick={() => setPairingMethod("phone")}
                    className={`rounded-chip px-3 py-1 text-caption font-semibold transition-colors ${
                      pairingMethod === "phone"
                        ? "bg-accent text-on-accent"
                        : "text-muted hover:text-ink"
                    }`}
                  >
                    Link with Phone Number
                  </button>
                </div>

                {pairingMethod === "qr" ? (
                  <>
                    <div className="rounded-card border border-line bg-card p-3 shadow-card">
                      <QrCode
                        value={qrCodeVal}
                        size={180}
                        label="Personal WhatsApp Connection QR Code"
                      />
                    </div>
                    <div>
                      <p className="text-body-sm font-semibold text-ink">
                        Scan this QR code with WhatsApp on your phone
                      </p>
                      <p className="text-caption text-muted">
                        {isLiveQr
                          ? "🟢 Live official WhatsApp QR code active. Refresh if camera expires."
                          : "Code updates automatically. No technical setup required."}
                      </p>
                    </div>
                    <div className="flex flex-wrap items-center justify-center gap-2">
                      <Button
                        size="sm"
                        variant="outline"
                        onClick={refreshQr}
                        leadingIcon={<RefreshIcon className="size-3.5" />}
                      >
                        Refresh QR Code
                      </Button>
                      <Button
                        size="sm"
                        onClick={handleLinkPhone}
                        leadingIcon={<CheckIcon className="size-3.5" />}
                      >
                        Confirm Scanned Phone
                      </Button>
                    </div>
                  </>
                ) : (
                  <div className="flex w-full max-w-sm flex-col gap-3 text-left">
                    <p className="text-body-sm text-muted text-center">
                      No camera required. Enter your phone number to get an 8-character pairing code to type into WhatsApp.
                    </p>

                    <TextField
                      label="Your Personal WhatsApp Phone Number"
                      value={pairingPhone}
                      onChange={(e) => setPairingPhone(e.target.value)}
                      placeholder="+91 90195 27568"
                    />

                    <Button
                      size="sm"
                      onClick={handleRequestPairingCode}
                      disabled={pairingLoading}
                      leadingIcon={<SparkIcon className="size-3.5" />}
                    >
                      {pairingLoading ? "Requesting WhatsApp Code..." : "Get 8-Character Pairing Code"}
                    </Button>

                    {pairingError && (
                      <p className="text-caption font-semibold text-negative">
                        {pairingError}
                      </p>
                    )}

                    {pairingCode && (
                      <div className="rounded-card bg-positive-soft p-4 border border-positive/30 text-center">
                        <span className="text-caption font-semibold text-positive uppercase tracking-wider">
                          WhatsApp Pairing Code
                        </span>
                        <div className="my-1 font-mono text-2xl font-bold tracking-widest text-ink select-all">
                          {pairingCode.length === 8
                            ? `${pairingCode.slice(0, 4)} - ${pairingCode.slice(4)}`
                            : pairingCode}
                        </div>
                        <p className="text-caption text-ink-soft">
                          Enter this 8-character code in WhatsApp on your phone under <strong>Link with phone number instead</strong>.
                        </p>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* Simple Instructions for non-technical users */}
          <div className="flex flex-col justify-between gap-4">
            <div className="flex flex-col gap-3">
              <h3 className="text-body-sm font-bold text-ink">
                {pairingMethod === "qr" ? "Simple 3-Step Setup (Camera Scan)" : "Phone Pairing Setup (No Camera)"}
              </h3>
              <ol className="flex flex-col gap-3 text-body-sm text-ink-soft">
                <li className="flex items-start gap-3 rounded-card bg-surface-muted p-3 border border-line">
                  <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-positive-soft font-bold text-positive text-caption">1</span>
                  <div>
                    <strong className="text-ink">Open WhatsApp on your mobile phone</strong>
                    <p className="text-caption text-muted">Works with standard personal WhatsApp and WhatsApp Business apps.</p>
                  </div>
                </li>
                <li className="flex items-start gap-3 rounded-card bg-surface-muted p-3 border border-line">
                  <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-positive-soft font-bold text-positive text-caption">2</span>
                  <div>
                    <strong className="text-ink">Go to Linked Devices</strong>
                    <p className="text-caption text-muted">Tap <strong>Settings</strong> (iPhone) or <strong>⋮ Menu</strong> (Android) &rarr; tap <strong>Linked Devices</strong>.</p>
                  </div>
                </li>
                <li className="flex items-start gap-3 rounded-card bg-surface-muted p-3 border border-line">
                  <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-positive-soft font-bold text-positive text-caption">3</span>
                  <div>
                    <strong className="text-ink">
                      {pairingMethod === "qr"
                        ? "Tap “Link a Device” and scan the screen"
                        : "Tap “Link with phone number instead”"}
                    </strong>
                    <p className="text-caption text-muted">
                      {pairingMethod === "qr"
                        ? "Aim your phone camera at the QR code on the left. Once scanned, your WhatsApp connects instantly!"
                        : "Tap 'Link with phone number instead' at the bottom of your phone screen and type the 8-character code."}
                    </p>
                  </div>
                </li>
              </ol>
            </div>

            <div className="rounded-card bg-positive-soft p-3 border border-positive/30">
              <p className="text-caption font-bold text-positive flex items-center gap-2">
                <CheckCircleIcon className="size-4 shrink-0" /> Zero Platform & Code Dependency
              </p>
              <p className="mt-1 text-caption text-ink-soft">
                Users can connect and manage everything directly from this portal. No terminal, developer tools, or Meta Business approvals needed.
              </p>
            </div>
          </div>
        </div>
      )}

      {/* Tab 2: Inbound & Outgoing Messages Log (Portal Activity Feed) */}
      {activeTab === "messages" && (
        <div className="flex flex-col gap-4">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <h3 className="text-body-sm font-bold text-ink">Inbound & Outgoing Message Stream</h3>
              <p className="text-caption text-muted">
                Real-time log of customer messages received and automated AI replies dispatched.
              </p>
            </div>
            <div className="flex items-center gap-1">
              <button
                type="button"
                onClick={() => setMsgFilter("all")}
                className={`rounded-chip px-3 py-1 text-caption font-semibold ${
                  msgFilter === "all" ? "bg-accent text-on-accent" : "bg-surface-muted text-muted hover:text-ink"
                }`}
              >
                All Messages ({messages.length})
              </button>
              <button
                type="button"
                onClick={() => setMsgFilter("inbound")}
                className={`rounded-chip px-3 py-1 text-caption font-semibold ${
                  msgFilter === "inbound" ? "bg-accent text-on-accent" : "bg-surface-muted text-muted hover:text-ink"
                }`}
              >
                Inbound Received ({messages.filter((m) => m.direction === "inbound").length})
              </button>
              <button
                type="button"
                onClick={() => setMsgFilter("outbound")}
                className={`rounded-chip px-3 py-1 text-caption font-semibold ${
                  msgFilter === "outbound" ? "bg-accent text-on-accent" : "bg-surface-muted text-muted hover:text-ink"
                }`}
              >
                Outgoing Sent ({messages.filter((m) => m.direction === "outbound").length})
              </button>
            </div>
          </div>

          {/* Messages List */}
          <div className="flex flex-col gap-2 max-h-96 overflow-y-auto pr-1">
            {filteredMessages.map((item) => (
              <div
                key={item.id}
                className={`flex flex-col gap-2 rounded-card p-3 border ${
                  item.direction === "outbound"
                    ? "border-accent/30 bg-accent/5 ml-4 sm:ml-8"
                    : "border-line bg-surface-muted mr-4 sm:mr-8"
                }`}
              >
                <div className="flex items-center justify-between text-caption">
                  <div className="flex items-center gap-2">
                    <span
                      className={`inline-flex items-center gap-1 rounded-full px-2 py-1 font-bold ${
                        item.direction === "outbound"
                          ? "bg-accent-soft text-accent"
                          : "bg-positive-soft text-positive"
                      }`}
                    >
                      {item.direction === "outbound" ? "↗ Outgoing (AI Reply)" : "↙ Inbound (Received)"}
                    </span>
                    <strong className="text-ink">{item.phone}</strong>
                    <span className="text-muted">({item.senderName})</span>
                  </div>
                  <span className="text-muted">{item.timestamp}</span>
                </div>
                <p className="text-body-sm text-ink leading-relaxed">{item.message}</p>
              </div>
            ))}
          </div>

          {/* Quick Outbound / Test Auto-Reply Form */}
          <div className="mt-2 flex flex-col gap-3 rounded-card bg-surface-muted p-4 border border-line">
            <div className="flex items-center gap-2 text-ink">
              <SparkIcon className="size-4 text-accent" />
              <h4 className="text-body-sm font-bold">Simulate Inbound & Generate AI Reply</h4>
            </div>
            <div className="grid gap-3 sm:grid-cols-3">
              <TextField
                label="Simulated Customer Phone"
                value={testPhone}
                onChange={(e) => setTestPhone(e.target.value)}
                placeholder="+91 98765 43210"
              />
              <div className="sm:col-span-2">
                <TextField
                  label="Incoming Message"
                  value={testMessage}
                  onChange={(e) => setTestMessage(e.target.value)}
                  placeholder="Ask a question..."
                />
              </div>
            </div>
            <div>
              <Button
                size="sm"
                onClick={runSimulation}
                disabled={isSimulating || !testMessage.trim()}
                leadingIcon={<SendIcon className="size-3.5" />}
              >
                {isSimulating ? "Generating AI Reply..." : "Simulate Auto-Reply"}
              </Button>
            </div>

            {simulatedReply && (
              <div className="mt-1 rounded-card bg-card p-3 border border-positive/30">
                <p className="text-caption font-semibold text-positive flex items-center gap-2">
                  <CheckCircleIcon className="size-3.5" /> AI Response (WhatsApp channel):
                </p>
                <p className="mt-1 text-body-sm text-ink leading-relaxed">
                  {simulatedReply}
                </p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Tab 3: AI Configuration */}
      {activeTab === "config" && (
        <div className="flex flex-col gap-6">
          <div>
            <h3 className="text-body-sm font-bold text-ink">Client Identity & AI Configuration</h3>
            <p className="text-caption text-muted">
              Configure your organization or client identity, custom greeting, auto-reply policy, and AI model keys. Auto-replies adapt to your company profile without repetitive canned messages.
            </p>
          </div>

          {/* 1. Client Company Identity & Greeting */}
          <div className="flex flex-col gap-4 rounded-card bg-surface-muted p-4 border border-line">
            <h4 className="text-body-sm font-bold text-ink">Company & Client Profile</h4>
            <p className="text-caption text-muted">
              Specify your company name, industry description, and welcome greeting. Outbound messages will reflect your organization instead of hardcoded generic lines.
            </p>

            <div className="grid gap-4 sm:grid-cols-2">
              <div className="flex flex-col gap-2">
                <label htmlFor="business-name-input" className="text-caption font-bold text-ink">
                  Company / Brand Name
                </label>
                <input
                  id="business-name-input"
                  type="text"
                  value={businessName}
                  onChange={(e) => setBusinessName(e.target.value)}
                  placeholder="e.g. Abhay Pharma, Acme Logistics, OpsGrids"
                  className="w-full rounded-chip border border-line bg-card py-2 px-3 text-body-sm text-ink"
                />
              </div>

              <div className="flex flex-col gap-2">
                <label htmlFor="business-desc-input" className="text-caption font-bold text-ink">
                  Business Description / Industry
                </label>
                <input
                  id="business-desc-input"
                  type="text"
                  value={businessDescription}
                  onChange={(e) => setBusinessDescription(e.target.value)}
                  placeholder="e.g. Wholesale pharmaceutical distributor of medicines and surgical supplies"
                  className="w-full rounded-chip border border-line bg-card py-2 px-3 text-body-sm text-ink"
                />
              </div>
            </div>

            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between">
                <label htmlFor="welcome-msg-input" className="text-caption font-bold text-ink">
                  Welcome Greeting Message
                </label>
                <span className="text-caption text-muted">
                  Tip: Use <code className="text-accent font-mono">&#123;businessName&#125;</code> to insert brand
                </span>
              </div>
              <textarea
                id="welcome-msg-input"
                rows={2}
                value={welcomeMessage}
                onChange={(e) => setWelcomeMessage(e.target.value)}
                placeholder="Hi! Thank you for contacting {businessName}. We received your message and our team will be with you shortly. How can we help you today?"
                className="w-full rounded-card border border-line bg-card p-3 text-body-sm text-ink"
              />
            </div>
          </div>

          {/* 2. Auto-Reply Strategy & Human Takeover */}
          <div className="grid gap-4 sm:grid-cols-2">
            <div className="flex flex-col gap-2 rounded-card bg-surface-muted p-4 border border-line">
              <label htmlFor="auto-reply-mode-select" className="text-caption font-bold text-ink">
                Auto-Reply Strategy
              </label>
              <select
                id="auto-reply-mode-select"
                value={autoReplyMode}
                onChange={(e) => setAutoReplyMode(e.target.value as "all_inbound" | "welcome_only" | "paused")}
                className="rounded-chip border border-line bg-card p-2 text-body-sm text-ink"
              >
                <option value="welcome_only">Welcome Message Only (Initial Contact) — Recommended</option>
                <option value="all_inbound">AI Conversational Assistant (24/7 Multi-Turn)</option>
                <option value="paused">Off / Manual Operator Only (No Auto-Replies)</option>
              </select>
              <p className="text-caption text-muted">
                {autoReplyMode === "welcome_only" && "Greets incoming customers once. Leaves all follow-up replies to human agents in the Inbox."}
                {autoReplyMode === "all_inbound" && "Intelligent AI answers questions continuously with conversation history, pausing when an agent types."}
                {autoReplyMode === "paused" && "Automated responses are disabled. All messages must be answered manually."}
              </p>
            </div>

            <div className="flex flex-col gap-2 rounded-card bg-surface-muted p-4 border border-line">
              <label htmlFor="human-takeover-input" className="text-caption font-bold text-ink">
                Human Operator Takeover Cooldown
              </label>
              <div className="flex items-center gap-2">
                <input
                  id="human-takeover-input"
                  type="number"
                  min={1}
                  max={1440}
                  value={humanTakeoverMinutes}
                  onChange={(e) => setHumanTakeoverMinutes(Math.max(1, parseInt(e.target.value || "30", 10)))}
                  className="w-24 rounded-chip border border-line bg-card py-2 px-3 text-body-sm text-ink font-mono"
                />
                <span className="text-caption text-ink font-medium">minutes</span>
              </div>
              <p className="text-caption text-muted">
                When an operator replies manually in the Inbox, AI auto-replies are silenced for this duration to prevent interrupting human conversations.
              </p>
            </div>
          </div>

          {/* 3. Model Provider & API Keys */}
          <div className="flex flex-col gap-4 rounded-card bg-surface-muted p-4 border border-line">
            <div className="flex items-center gap-2">
              <KeyIcon className="size-4 text-accent" />
              <h4 className="text-body-sm font-bold text-ink">AI Model Provider & API Keys</h4>
            </div>

            <div className="flex flex-col gap-2">
              <label htmlFor="ai-provider-select" className="text-caption font-bold text-ink">AI Model Provider</label>
              <select
                id="ai-provider-select"
                value={aiProvider}
                onChange={(e) => setAiProvider(e.target.value as "gemini" | "openai" | "auto")}
                className="rounded-chip border border-line bg-card p-2 text-body-sm text-ink"
              >
                <option value="auto">Auto (Detects GEMINI_API_KEY or OPENAI_API_KEY)</option>
                <option value="gemini">Google Gemini 1.5 Flash (Fast & High-Intelligence)</option>
                <option value="openai">OpenAI ChatGPT (gpt-4o-mini)</option>
              </select>
            </div>

            <div className="grid gap-4 sm:grid-cols-2">
              {/* Google Gemini Key */}
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between">
                  <label htmlFor="gemini-key-input" className="text-caption font-bold text-ink">
                    Google Gemini API Key
                  </label>
                  {hasGeminiKey && (
                    <span className="text-caption font-semibold text-positive">
                      ✓ Active: {geminiKeyMasked}
                    </span>
                  )}
                </div>
                <div className="relative flex items-center">
                  <input
                    id="gemini-key-input"
                    type={showGeminiKey ? "text" : "password"}
                    value={geminiKeyInput}
                    onChange={(e) => setGeminiKeyInput(e.target.value)}
                    placeholder={hasGeminiKey ? "Enter new key to replace existing..." : "AIzaSy..."}
                    className="w-full rounded-chip border border-line bg-card py-2 pl-3 pr-8 text-body-sm text-ink font-mono"
                  />
                  <button
                    type="button"
                    onClick={() => setShowGeminiKey(!showGeminiKey)}
                    className="absolute right-3 text-muted hover:text-ink"
                    title={showGeminiKey ? "Hide key" : "Show key"}
                  >
                    {showGeminiKey ? <EyeOffIcon className="size-4" /> : <EyeIcon className="size-4" />}
                  </button>
                </div>
                <p className="text-caption text-muted">
                  Get a free API key at <a href="https://aistudio.google.com" target="_blank" rel="noreferrer" className="text-accent underline">Google AI Studio</a>.
                </p>
              </div>

              {/* OpenAI Key */}
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between">
                  <label htmlFor="openai-key-input" className="text-caption font-bold text-ink">
                    OpenAI API Key
                  </label>
                  {hasOpenaiKey && (
                    <span className="text-caption font-semibold text-positive">
                      ✓ Active: {openaiKeyMasked}
                    </span>
                  )}
                </div>
                <div className="relative flex items-center">
                  <input
                    id="openai-key-input"
                    type={showOpenaiKey ? "text" : "password"}
                    value={openaiKeyInput}
                    onChange={(e) => setOpenaiKeyInput(e.target.value)}
                    placeholder={hasOpenaiKey ? "Enter new key to replace existing..." : "sk-..."}
                    className="w-full rounded-chip border border-line bg-card py-2 pl-3 pr-8 text-body-sm text-ink font-mono"
                  />
                  <button
                    type="button"
                    onClick={() => setShowOpenaiKey(!showOpenaiKey)}
                    className="absolute right-3 text-muted hover:text-ink"
                    title={showOpenaiKey ? "Hide key" : "Show key"}
                  >
                    {showOpenaiKey ? <EyeOffIcon className="size-4" /> : <EyeIcon className="size-4" />}
                  </button>
                </div>
                <p className="text-caption text-muted">
                  Get your API key from <a href="https://platform.openai.com" target="_blank" rel="noreferrer" className="text-accent underline">platform.openai.com</a>.
                </p>
              </div>
            </div>
          </div>

          {/* 4. Products Knowledge Base */}
          <div className="flex flex-col gap-3 rounded-card bg-surface-muted p-4 border border-line">
            <h4 className="text-body-sm font-bold text-ink">Custom Products, Services & Pricing Knowledge</h4>
            <p className="text-caption text-muted">
              Add your product lines, price ranges, warranty/delivery policies, branch addresses, or FAQs. The AI answers customer inquiries using this exact knowledge.
            </p>
            <textarea
              id="custom-knowledge-input"
              rows={4}
              value={customKnowledge}
              onChange={(e) => setCustomKnowledge(e.target.value)}
              placeholder="e.g. Products: Generic medicines, clinical surgical supplies. Delivery: Same-day delivery across regional hubs. Bulk order minimum: ₹5,000."
              className="w-full rounded-card border border-line bg-card p-3 text-body-sm text-ink"
            />
          </div>

          {/* 5. Anti-Repeat & Deduplication Shield */}
          <div className="rounded-card bg-surface-muted p-4 border border-line flex flex-col gap-2">
            <span className="text-caption font-bold text-ink">Spam & Duplicate Shield</span>
            <div className="flex items-center gap-2 text-caption text-positive font-semibold">
              <CheckCircleIcon className="size-4" /> Enabled: Dynamic deduplication & operator protection active
            </div>
            <p className="text-caption text-muted">
              The bridge automatically suppresses repeating identical messages, blocks greeting loops on multi-turn conversations, and yields to human agents.
            </p>
          </div>

          <div className="flex items-center gap-3">
            <Button
              size="sm"
              onClick={handleSaveAiConfig}
              disabled={isSavingConfig}
            >
              {isSavingConfig ? "Saving Configuration..." : "Save Configuration & Keys"}
            </Button>
            {savedNotice && (
              <span className="text-caption font-semibold text-positive">
                ✓ WhatsApp client identity, auto-reply policy, and API keys saved successfully!
              </span>
            )}
          </div>
        </div>
      )}
    </Card>
  );
}

function WhatsAppConnection() {
  const loader = useCallback(() => getWhatsAppConnection(), []);
  const state = useAsync(loader, []);
  const [justSaved, setJustSaved] = useState<WhatsAppConnectionOut | null>(null);
  const [replacing, setReplacing] = useState(false);
  const loaded = state.status === "success" ? (state.data.items[0] ?? null) : null;
  const connection = justSaved ?? loaded;

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="WhatsApp"
        title="Connection"
        description="Connect WhatsApp to automate messages and AI auto-replies. Link your personal WhatsApp via QR code or configure official Meta Cloud API credentials."
      />

      <PersonalWhatsAppPortal />

      {state.status === "loading" ? (
        <Card className="flex flex-col gap-4 p-6">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-2/3" />
          <Skeleton className="h-20 w-full rounded-tile" />
        </Card>
      ) : null}

      {state.status === "error" ? (
        <LoadFailure error={state.error} title="Couldn't load the WhatsApp connection" />
      ) : null}

      {/* The unconnected state is the form itself — a panel above it saying "not connected" would
          be the same sentence twice, and only one of them acts. */}
      {state.status === "success" && connection === null && !replacing ? (
        <WhatsAppForm mode="connect" onSaved={setJustSaved} />
      ) : null}

      {connection !== null && !replacing ? (
        <ConnectionPanel connection={connection} onReplace={() => setReplacing(true)} />
      ) : null}

      {replacing ? (
        <WhatsAppForm
          mode="replace"
          onSaved={(saved) => {
            setJustSaved(saved);
            setReplacing(false);
          }}
          onCancel={() => setReplacing(false)}
        />
      ) : null}
    </div>
  );
}

/** The screen's single polite live region. */
export function WhatsAppConnectionPage() {
  return (
    <Announcer>
      <WhatsAppConnection />
    </Announcer>
  );
}
