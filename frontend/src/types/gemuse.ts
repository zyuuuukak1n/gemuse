export type ThinkingLevel = "auto" | "low" | "medium" | "high" | "xhigh";

export type WorkspaceTab = "chat" | "browser" | "dashboard" | "memory" | "settings";

export type JobStatus =
  | "queued"
  | "running"
  | "waiting_approval"
  | "paused_manual"
  | "completed"
  | "failed";

export interface SSEMetadataData {
  job_id: string;
  conversation_id: string;
  thinking_level: ThinkingLevel;
  routing_reason: string;
}

export interface SSEThinkingData {
  stage?: string;
  summary?: string;
  text?: string;
}

export interface SSEToolCallData {
  tool_name: string;
  status: "started" | "completed" | "error" | "blocked_for_approval" | string;
  output_preview?: string;
  error?: string;
}

export interface SSEDeltaData {
  text: string;
}

export interface SSEApprovalRequiredData {
  approval_id: string;
  job_id?: string;
  action_type: string;
  payload: Record<string, unknown>;
  expires_at: string;
  one_time_token: string;
}

export interface SSEDoneData {
  job_id: string;
  conversation_id: string;
  answer: string;
  citations: string[];
}

export interface SSEErrorData {
  code?: string;
  message: string;
}

export type SSEEvent =
  | { event: "metadata"; data: SSEMetadataData }
  | { event: "thinking"; data: SSEThinkingData }
  | { event: "tool_call"; data: SSEToolCallData }
  | { event: "delta"; data: SSEDeltaData }
  | { event: "approval_required"; data: SSEApprovalRequiredData }
  | { event: "done"; data: SSEDoneData }
  | { event: "error"; data: SSEErrorData };

export interface ApprovalCardData extends SSEApprovalRequiredData {
  decision_status: "pending" | "approved" | "rejected" | "executed";
  execution_output?: string;
  error_message?: string;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  thinking_summary?: string | null;
  tool_calls?: SSEToolCallData[];
  citations?: string[];
  attachments?: string[];
  approval?: ApprovalCardData;
  is_streaming?: boolean;
  created_at: string;
}

export interface ConversationSummary {
  id: string;
  title: string;
  latest_job_status: JobStatus | null;
  latest_job_id: string | null;
  thinking_level: ThinkingLevel;
  created_at: string;
  updated_at: string;
}

export interface ConversationDetail {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  messages: ChatMessage[];
  jobs: Array<{
    job_id: string;
    status: JobStatus;
    thinking_level: ThinkingLevel;
    created_at: string;
  }>;
}

export interface JobSummary {
  job_id: string;
  conversation_id: string;
  status: JobStatus;
  thinking_level: ThinkingLevel;
  prompt_preview?: string;
  result_summary?: string | null;
  error_message?: string | null;
  latest_citations?: string[];
  created_at: string;
  updated_at: string;
}

export interface BrowserDownloadItem {
  filename: string;
  size_bytes: number;
  modified_at: string;
}

export interface BrowserAuditEntry {
  id: string;
  job_id: string | null;
  session_id: string | null;
  action: string;
  target_url: string | null;
  screenshot_path: string | null;
  operator: string;
  details: Record<string, unknown>;
  created_at: string;
}

export interface BrowserSessionStatus {
  session_id: string;
  paused: boolean;
  pause_info: {
    reason?: string;
    paused_at?: string;
    detected_price?: string;
    url?: string;
  } | null;
  stopped: boolean;
  novnc_url: string;
  downloads: BrowserDownloadItem[];
  url?: string;
  title?: string;
  active?: boolean;
}

export interface ProactiveNotificationItem {
  id: string;
  rule_id: string;
  event_hash?: string;
  priority: string;
  title: string;
  body: string;
  channels_sent?: string[];
  is_read: boolean;
  created_at: string;
}

export interface MCPConnectorStatus {
  connector: string;
  configured: boolean;
  status: string;
  tools: string[];
  active_tools?: string[];
}

export interface DiscordPermissionServer {
  guild_id: string;
  name: string;
  permission: "disabled" | "read_only" | "read_write";
  allowed_channels?: string[];
}

export interface DashboardSummary {
  generated_at: string;
  jobs: JobSummary[];
  pending_approvals: Array<{
    approval_id: string;
    job_id: string | null;
    action_type: string;
    payload: Record<string, unknown>;
    expires_at: string;
  }>;
  notifications: ProactiveNotificationItem[];
  connectors: MCPConnectorStatus[];
  discord: {
    default_policy: string;
    configured: boolean;
    servers: DiscordPermissionServer[];
  };
  calendar_events: Array<{
    id: string;
    summary: string;
    start: string;
    end: string;
  }>;
  emails: Array<{
    provider: string;
    message_id: string;
    subject: string;
    sender: string;
    snippet: string;
    received_at?: string;
  }>;
}

export interface EpisodeMemoryItem {
  id: string;
  content: string;
  source_type: string;
  source_ref: string;
  importance_score: number;
  similarity: number | null;
  created_at: string;
}

export interface SkillMemoryItem {
  id: string;
  name: string;
  description: string;
  trigger_keywords: string[];
  steps_markdown: string;
  usage_count: number;
  updated_at: string;
}

export interface HandoffPackageData {
  conversation_id: string | null;
  conversation_title: string;
  message_count: number;
  handoff_prompt: string;
}

export interface TakeoutImportResultData {
  format_detected: string;
  parsed_count: number;
  imported_count: number;
  skipped_duplicates: number;
}
