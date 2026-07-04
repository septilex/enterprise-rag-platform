import type { ChatMessage } from "../types";
import { Citations } from "./Citations";

// Renders one chat turn. Assistant turns show citations (UI-02), a low-confidence
// indicator when not grounded (UI-09), and feedback controls (UI-05).
export function Message({
  message,
  onFeedback,
}: {
  message: ChatMessage;
  onFeedback?: (rating: "up" | "down") => void;
}) {
  const isUser = message.role === "user";
  return (
    <div className={`msg ${isUser ? "msg-user" : "msg-assistant"}`}>
      <div className="msg-role">{isUser ? "You" : "Assistant"}</div>
      <div className="msg-body">
        {message.content || (message.streaming ? "…" : "")}
        {message.streaming && <span className="cursor">▌</span>}
      </div>

      {!isUser && !message.streaming && message.grounded === false && (
        <div className="no-answer" role="status">
          ⚠️ No grounded answer — no source passed the confidence threshold.
        </div>
      )}

      {!isUser && <Citations citations={message.citations} />}

      {!isUser && !message.streaming && onFeedback && (
        <div className="feedback">
          <button onClick={() => onFeedback("up")} aria-label="Helpful">👍</button>
          <button onClick={() => onFeedback("down")} aria-label="Not helpful">👎</button>
        </div>
      )}
    </div>
  );
}
