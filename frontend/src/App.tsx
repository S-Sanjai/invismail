import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  ArrowDownToLine,
  Check,
  CheckCheck,
  ChevronDown,
  CircleHelp,
  Clock3,
  Copy,
  File,
  Github,
  Linkedin,
  LoaderCircle,
  Mail,
  MailOpen,
  Moon,
  RefreshCw,
  Search,
  Sun,
  Trash2,
  X,
} from "lucide-react";
import {
  createInbox,
  deleteMessage,
  downloadAttachment,
  getMessage,
  listMessages,
  type EmailDetail,
  type EmailListItem,
  type InboxSession,
} from "./lib/api";

const STORAGE_KEY = "invismail.inbox.v1";
const THEME_KEY = "invismail.theme.v1";
const GITHUB_URL = "https://github.com/S-Sanjai";
const LINKEDIN_URL = "https://www.linkedin.com/in/sanjai-s/";
type Theme = "dark" | "light";

const TTL_OPTIONS = [
  { label: "15 minutes", value: 900 },
  { label: "1 hour", value: 3600 },
  { label: "3 hours", value: 10800 },
  { label: "24 hours", value: 86400 },
];

function readTheme(): Theme {
  try {
    const stored = localStorage.getItem(THEME_KEY);
    if (stored === "light" || stored === "dark") return stored;
  } catch {
    // Ignore storage access failures and fall back to the system preference.
  }
  return window.matchMedia?.("(prefers-color-scheme: light)").matches ? "light" : "dark";
}

function readStoredInbox(): InboxSession | null {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    if (!value) return null;
    const parsed = JSON.parse(value) as InboxSession;
    if (!parsed.access_token || !parsed.email_address || !parsed.expires_at) return null;
    if (new Date(parsed.expires_at).getTime() <= Date.now()) {
      localStorage.removeItem(STORAGE_KEY);
      return null;
    }
    return parsed;
  } catch {
    localStorage.removeItem(STORAGE_KEY);
    return null;
  }
}

function formatTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatFullDate(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function formatRemaining(expiresAt: string, now: number): string {
  const remaining = Math.max(0, Math.floor((new Date(expiresAt).getTime() - now) / 1000));
  const hours = Math.floor(remaining / 3600);
  const minutes = Math.floor((remaining % 3600) / 60);
  const seconds = remaining % 60;
  if (hours) return `${hours}h ${String(minutes).padStart(2, "0")}m`;
  return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
}

function initials(value: string): string {
  const clean = value.split("@")[0]?.replace(/[^a-z0-9]/gi, "") ?? "M";
  return clean.slice(0, 1).toUpperCase() || "M";
}

function prettySize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const EMAIL_DOCUMENT_STYLE =
  ':root{color-scheme:light}body{margin:0;font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;color:#1f241c;background:#ffffff;padding:24px;font-size:15px;line-height:1.65;overflow-wrap:anywhere}img{max-width:100%;height:auto}a{color:#5b21b6}';

// Rewrite every link in the message so it opens in a real browser tab instead of
// navigating inside the sandboxed preview frame.
function buildEmailDocument(html: string): string {
  const parsed = new DOMParser().parseFromString(html, "text/html");

  parsed.querySelectorAll("base").forEach((node) => node.remove());
  parsed.querySelectorAll("a[href], area[href]").forEach((node) => {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noreferrer noopener");
  });
  parsed.querySelectorAll("form").forEach((node) => node.setAttribute("target", "_blank"));

  const emailStyles = Array.from(parsed.querySelectorAll("head style"))
    .map((node) => node.outerHTML)
    .join("");

  return `<!doctype html><html><head><meta charset="utf-8"><base target="_blank"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; img-src data: cid:; style-src 'unsafe-inline'; font-src data:"><style>${EMAIL_DOCUMENT_STYLE}</style>${emailStyles}</head><body>${parsed.body.innerHTML}</body></html>`;
}

function App() {
  const [inbox, setInbox] = useState<InboxSession | null>(() => readStoredInbox());
  const [theme, setTheme] = useState<Theme>(() => readTheme());
  const createStarted = useRef(false);
  const [ttl, setTtl] = useState(3600);
  const [messages, setMessages] = useState<EmailListItem[]>([]);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [selectedMessage, setSelectedMessage] = useState<EmailDetail | null>(null);
  const [loadingInbox, setLoadingInbox] = useState(!readStoredInbox());
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [creating, setCreating] = useState(false);
  const [search, setSearch] = useState("");
  const [now, setNow] = useState(Date.now());
  const [copied, setCopied] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [mobileView, setMobileView] = useState<"list" | "message">("list");
  const [refreshing, setRefreshing] = useState(false);
  const [aboutOpen, setAboutOpen] = useState(false);
  const refreshInFlight = useRef(false);

  const remaining = inbox ? formatRemaining(inbox.expires_at, now) : "--:--";

  const filteredMessages = useMemo(() => {
    const term = search.trim().toLowerCase();
    if (!term) return messages;
    return messages.filter((message) =>
      [message.sender, message.subject].some((value) => value.toLowerCase().includes(term)),
    );
  }, [messages, search]);

  const createNewInbox = useCallback(async (nextTtl = ttl) => {
    setCreating(true);
    setError("");
    setNotice("");
    try {
      const created = {
        ...(await createInbox(nextTtl)),
        created_at: new Date().toISOString(),
        ttl_seconds: nextTtl,
      };
      localStorage.setItem(STORAGE_KEY, JSON.stringify(created));
      setInbox(created);
      setMessages([]);
      setSelectedId(null);
      setSelectedMessage(null);
      setMobileView("list");
      return created;
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not create an inbox.");
      return null;
    } finally {
      setCreating(false);
      setLoadingInbox(false);
    }
  }, [ttl]);

  const refreshMessages = useCallback(async () => {
    if (!inbox || refreshInFlight.current || new Date(inbox.expires_at).getTime() <= Date.now()) return;
    refreshInFlight.current = true;
    try {
      const response = await listMessages(inbox.access_token);
      setMessages(response.items);
      setError("");
      if (response.items.length > 0) {
        setSelectedId((current) => current ?? response.items[0].id);
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unable to refresh this inbox.");
    } finally {
      refreshInFlight.current = false;
    }
  }, [inbox]);

  useEffect(() => {
    if (!inbox && !createStarted.current) {
      createStarted.current = true;
      void createNewInbox(ttl);
    } else if (inbox) {
      setLoadingInbox(false);
    }
    // Restore or create the browser's current inbox once on mount.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#0e0d13" : "#f4f3f9");
    try {
      localStorage.setItem(THEME_KEY, theme);
    } catch {
      // Ignore storage access failures; the theme still applies for this session.
    }
  }, [theme]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  useEffect(() => {
    if (!inbox) return;
    void refreshMessages();
    const poll = window.setInterval(() => void refreshMessages(), 5000);
    return () => window.clearInterval(poll);
  }, [inbox, refreshMessages]);

  useEffect(() => {
    if (!inbox || !selectedId) {
      setSelectedMessage(null);
      return;
    }
    let cancelled = false;
    setLoadingDetail(true);
    getMessage(inbox.access_token, selectedId)
      .then((message) => {
        if (!cancelled) {
          setSelectedMessage(message);
          setMessages((current) =>
            current.map((item) => item.id === message.id ? { ...item, is_read: true } : item),
          );
        }
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          setSelectedMessage(null);
          setError(err instanceof Error ? err.message : "Unable to open this message.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingDetail(false);
      });
    return () => { cancelled = true; };
  }, [inbox, selectedId]);

  useEffect(() => {
    if (!aboutOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setAboutOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [aboutOpen]);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(""), 2400);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const handleRefresh = async () => {
    setRefreshing(true);
    await refreshMessages();
    setRefreshing(false);
  };

  const copyAddress = async () => {
    if (!inbox) return;
    try {
      await navigator.clipboard.writeText(inbox.email_address);
      setCopied(true);
      setNotice("Address copied to clipboard");
      window.setTimeout(() => setCopied(false), 1800);
    } catch {
      setError("Clipboard access was blocked by your browser.");
    }
  };

  const handleDelete = async () => {
    if (!inbox || !selectedMessage) return;
    try {
      await deleteMessage(inbox.access_token, selectedMessage.id);
      setMessages((current) => current.filter((item) => item.id !== selectedMessage.id));
      setSelectedMessage(null);
      setSelectedId(null);
      setNotice("Message deleted");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete the message.");
    }
  };

  const handleDownload = async (attachmentId: string, filename: string) => {
    if (!inbox) return;
    try {
      const blob = await downloadAttachment(inbox.access_token, attachmentId);
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Attachment download failed.");
    }
  };

  const handleNewAddress = async () => {
    await createNewInbox(ttl);
  };

  const htmlDocument = useMemo(
    () => (selectedMessage?.html_body ? buildEmailDocument(selectedMessage.html_body) : ""),
    [selectedMessage?.html_body],
  );

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="InvisMail home">
          <span className="brand-name"><span className="brand-invis">Invis</span><span className="brand-mail">Mail</span></span>
        </a>
        <div className="topbar-right">
          <a
            className="icon-button github-button"
            href={GITHUB_URL}
            target="_blank"
            rel="noreferrer"
            title="GitHub"
            aria-label="GitHub profile"
          >
            <Github size={17} />
          </a>
          <button
            className="icon-button theme-button"
            title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
            aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
          >
            {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
          </button>
          <button
            className="icon-button help-button"
            title="About InvisMail"
            aria-label="About InvisMail"
            aria-expanded={aboutOpen}
            onClick={() => setAboutOpen((open) => !open)}
          >
            <CircleHelp size={17} />
          </button>
        </div>
      </header>

      <main className="workspace">
        <section className="main-panel">
          <h1 className="sr-only">InvisMail temporary inbox</h1>

          <section className="inbox-card">
            <div className="inbox-toolbar">
              <div className="address-line">
                <span className="address-at">@</span>
                <span className="address-value">
                  {loadingInbox ? "Creating your address…" : inbox?.email_address ?? "Inbox unavailable"}
                </span>
                <button className="copy-button" onClick={() => void copyAddress()} disabled={!inbox} aria-label="Copy email address">
                  {copied ? <Check size={16} /> : <Copy size={16} />}
                  <span>{copied ? "Copied" : "Copy"}</span>
                </button>
                <button
                  className="icon-button regenerate-button"
                  onClick={() => void handleNewAddress()}
                  disabled={creating}
                  title="Generate a new address"
                  aria-label="Generate a new address"
                >
                  {creating ? <LoaderCircle className="spin" size={16} /> : <RefreshCw size={16} />}
                </button>
              </div>
              <div className="toolbar-actions">
                <label className="ttl-control">
                  <Clock3 size={15} />
                  <span>Expires in</span>
                  <strong>{remaining}</strong>
                  <select
                    value={ttl}
                    onChange={(event) => setTtl(Number(event.target.value))}
                    aria-label="Lifetime for the next address"
                    title="Applied when you generate a new address"
                  >
                    {TTL_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>{option.label}</option>
                    ))}
                  </select>
                </label>
              </div>
              <div className="expiry-track">
                <span style={{ width: inbox ? `${Math.max(2, Math.min(100, ((new Date(inbox.expires_at).getTime() - now) / (inbox.ttl_seconds ?? ttl) / 1000) * 100))}%` : "0%" }} />
              </div>
            </div>

            <div className="mail-layout">
              <section className={`message-list-pane ${mobileView === "message" ? "mobile-hidden" : ""}`}>
                <div className="list-header">
                  <div className="list-title">Inbox <span className="count-pill">{messages.length}</span></div>
                  <button
                    className={`icon-button refresh-button ${refreshing ? "is-refreshing" : ""}`}
                    onClick={() => void handleRefresh()}
                    disabled={!inbox || refreshing}
                    title="Refresh inbox"
                    aria-label="Refresh inbox"
                  >
                    <RefreshCw size={16} />
                  </button>
                </div>
                <div className="search-box">
                  <Search size={16} />
                  <input value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Search inbox" aria-label="Search your inbox" />
                  {search && <button onClick={() => setSearch("")} aria-label="Clear search"><X size={14} /></button>}
                </div>
                <div className="message-list">
                  {loadingInbox ? (
                    <div className="list-loading"><LoaderCircle size={20} className="spin" /> Preparing your inbox</div>
                  ) : filteredMessages.length === 0 ? (
                    <div className="empty-list">
                      <div className="empty-mail-icon"><MailOpen size={23} /></div>
                      <strong>{search ? "No matching messages" : "Nothing here yet"}</strong>
                      <p>{search ? "Try a different sender or subject." : "New messages will appear here automatically."}</p>
                    </div>
                  ) : filteredMessages.map((message) => (
                    <button
                      className={`message-row ${selectedId === message.id ? "selected" : ""} ${!message.is_read ? "unread" : ""}`}
                      key={message.id}
                      onClick={() => { setSelectedId(message.id); setMobileView("message"); }}
                    >
                      <span className="sender-avatar">{initials(message.sender)}</span>
                      <span className="message-row-content">
                        <span className="message-row-top"><strong>{message.sender}</strong><time>{formatTime(message.received_at)}</time></span>
                        <span className="message-subject">{message.subject || "(No subject)"}</span>
                      </span>
                      {!message.is_read && <span className="unread-dot" />}
                    </button>
                  ))}
                </div>
              </section>

              <section className={`reading-pane ${mobileView === "list" ? "mobile-hidden" : ""}`}>
                {!selectedId ? (
                  <div className="reading-empty">
                    <div className="empty-illustration"><Mail size={26} /></div>
                    <h2>Your inbox is ready</h2>
                    <p>Send an email to your temporary address. It will show up here as soon as it arrives.</p>
                  </div>
                ) : loadingDetail ? (
                  <div className="reading-empty"><LoaderCircle className="spin" size={24} /><p>Opening message…</p></div>
                ) : selectedMessage ? (
                  <article className="email-detail">
                    <div className="detail-toolbar">
                      <button className="back-button" onClick={() => setMobileView("list")}><ChevronDown size={16} /> Back to inbox</button>
                      <div className="detail-actions">
                        <button className="icon-button" title="Delete message" aria-label="Delete message" onClick={() => void handleDelete()}><Trash2 size={17} /></button>
                      </div>
                    </div>
                    <div className="email-content">
                      <h2 className="email-subject">{selectedMessage.subject || "(No subject)"}</h2>
                      <div className="sender-block">
                        <div className="sender-avatar large">{initials(selectedMessage.sender)}</div>
                        <div className="sender-meta"><strong>{selectedMessage.sender}</strong><span>To: {selectedMessage.recipient}</span></div>
                        <time>{formatFullDate(selectedMessage.received_at)}</time>
                      </div>
                      <div className="email-body">
                        {selectedMessage.html_body ? (
                          <iframe
                            className="html-email-frame"
                            title="Email content"
                            sandbox="allow-popups allow-popups-to-escape-sandbox"
                            referrerPolicy="no-referrer"
                            srcDoc={htmlDocument}
                          />
                        ) : selectedMessage.text_body ? (
                          <pre className="plain-email">{selectedMessage.text_body}</pre>
                        ) : (
                          <div className="no-body">This message doesn&apos;t contain a text body.</div>
                        )}
                      </div>
                      {selectedMessage.attachments.length > 0 && (
                        <div className="attachments">
                          <div className="attachments-heading"><span>Attachments</span><span>{selectedMessage.attachments.length}</span></div>
                          {selectedMessage.attachments.map((attachment) => (
                            <button key={attachment.id} className="attachment-row" onClick={() => void handleDownload(attachment.id, attachment.filename)}>
                              <span className="attachment-icon"><File size={18} /></span>
                              <span className="attachment-name"><strong>{attachment.filename}</strong><small>{prettySize(attachment.size)} · {attachment.content_type}</small></span>
                              <ArrowDownToLine size={17} />
                            </button>
                          ))}
                        </div>
                      )}
                    </div>
                  </article>
                ) : (
                  <div className="reading-empty"><AlertCircle size={24} /><h2>Couldn&apos;t open this message</h2><p>Refresh the inbox and try again.</p></div>
                )}
              </section>
            </div>
          </section>
        </section>
      </main>

      {aboutOpen && (
        <div className="about-layer" onClick={() => setAboutOpen(false)}>
          <section
            className="about-card"
            role="dialog"
            aria-modal="true"
            aria-labelledby="about-title"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="about-head">
              <h2 id="about-title">About InvisMail</h2>
              <button className="icon-button" onClick={() => setAboutOpen(false)} aria-label="Close">
                <X size={15} />
              </button>
            </div>
            <p className="about-copy">
              A self-hosted disposable inbox: InvisMail generates a temporary address that expires after a set
              lifetime, receives mail through a local SMTP adapter or a Mailgun inbound webhook, and parses each
              message in a FastAPI worker so it appears here on its own. Inboxes, messages and attachments live in
              PostgreSQL and local storage, tied only to the access token this browser holds.
            </p>
            <div className="about-links">
              <a href={GITHUB_URL} target="_blank" rel="noreferrer">
                <Github size={15} /> GitHub
              </a>
              {LINKEDIN_URL && (
                <a href={LINKEDIN_URL} target="_blank" rel="noreferrer">
                  <Linkedin size={15} /> LinkedIn
                </a>
              )}
            </div>
          </section>
        </div>
      )}

      {(error || notice) && (
        <div className={`toast ${error ? "toast-error" : ""}`} role="status">
          {error ? <AlertCircle size={17} /> : <CheckCheck size={17} />}
          <span>{error || notice}</span>
          <button onClick={() => { setError(""); setNotice(""); }} aria-label="Dismiss"><X size={15} /></button>
        </div>
      )}
    </div>
  );
}

export default App;
