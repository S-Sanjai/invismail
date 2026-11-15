export interface InboxSession {
  id: string;
  email_address: string;
  access_token: string;
  expires_at: string;
  status: string;
  created_at?: string;
  ttl_seconds?: number;
}

export interface EmailListItem {
  id: string;
  sender: string;
  subject: string;
  received_at: string;
  is_read: boolean;
}

export interface EmailAttachment {
  id: string;
  filename: string;
  content_type: string;
  size: number;
}

export interface EmailDetail extends EmailListItem {
  recipient: string;
  text_body: string | null;
  html_body: string | null;
  attachments: EmailAttachment[];
}

interface MessageListResponse {
  items: EmailListItem[];
  page: number;
  limit: number;
  total: number;
}

const API_BASE = import.meta.env.VITE_API_BASE_URL ?? "";

async function request<T>(
  path: string,
  options: RequestInit = {},
  token?: string,
): Promise<T> {
  const headers = new Headers(options.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (options.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetch(`${API_BASE}${path}`, {
    ...options,
    headers,
    credentials: "omit",
  });

  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch {
      // Keep the generic HTTP status if the response isn't JSON.
    }
    throw new Error(detail);
  }

  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export async function createInbox(ttlSeconds: number): Promise<InboxSession> {
  return request<InboxSession>("/api/v1/inboxes", {
    method: "POST",
    body: JSON.stringify({ ttl_seconds: ttlSeconds }),
  });
}

export async function listMessages(token: string): Promise<MessageListResponse> {
  return request<MessageListResponse>(
    "/api/v1/inboxes/me/messages?page=1&limit=100",
    {},
    token,
  );
}

export async function getMessage(token: string, messageId: string): Promise<EmailDetail> {
  return request<EmailDetail>(`/api/v1/messages/${encodeURIComponent(messageId)}`, {}, token);
}

export async function deleteMessage(token: string, messageId: string): Promise<void> {
  return request<void>(
    `/api/v1/messages/${encodeURIComponent(messageId)}`,
    { method: "DELETE" },
    token,
  );
}

export async function downloadAttachment(
  token: string,
  attachmentId: string,
): Promise<Blob> {
  const response = await fetch(
    `${API_BASE}/api/v1/attachments/${encodeURIComponent(attachmentId)}/download`,
    { headers: { Authorization: `Bearer ${token}` }, credentials: "omit" },
  );

  if (!response.ok) {
    throw new Error(`Attachment download failed (${response.status})`);
  }

  return response.blob();
}
