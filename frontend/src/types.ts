export interface Citation {
  index: number;
  chunk_id: string;
  document_id: string;
  chunk_index: number;
  snippet: string;
}

export interface Collection {
  id: string;
  tenant_id: string;
  name: string;
}

export interface ChatSession {
  id: string;
  tenant_id: string;
  collection_id: string | null;
  user_id: string;
  title: string;
  created_at: string;
}

export interface ChatMessage {
  id?: string;
  role: "user" | "assistant";
  content: string;
  citations: Citation[];
  grounded?: boolean;
  streaming?: boolean;
}
