import { useState } from "react";
import type { ChatMessage } from "../types";
import { Citations } from "./Citations";

// One chat turn. Assistant turns show citations (UI-02), a low-confidence
// indicator when not grounded (UI-09), and feedback controls (UI-05).
export function Message({
  message,
  onFeedback,
}: {
  message: ChatMessage;
  onFeedback?: (rating: "up" | "down") => void;
}) {
  const isUser = message.role === "user";
  const [rated, setRated] = useState<"up" | "down" | null>(null);

  return (
    <div className={`msg ${isUser ? "msg-user" : "msg-assistant"}`}>
      <div className="msg-avatar">{isUser ? "You" : "◈"}</div>
      <div className="msg-content">
        <div className="msg-body">
          {message.content || (message.streaming ? "" : "")}
          {message.streaming && <span className="cursor" />}
        </div>

        {!isUser && !message.streaming && message.grounded === false && (
          <div className="no-answer" role="status">
            <strong>No grounded answer.</strong> Nothing in this collection passed
            the confidence threshold. Try rephrasing or uploading a relevant document.
          </div>
        )}

        {!isUser && <Citations citations={message.citations} />}

        {!isUser && !message.streaming && onFeedback && (
          <div className="feedback">
            <button
              className={rated === "up" ? "active" : ""}
              onClick={() => { onFeedback("up"); setRated("up"); }}
              aria-label="Helpful"
            >👍</button>
            <button
              className={rated === "down" ? "active" : ""}
              onClick={() => { onFeedback("down"); setRated("down"); }}
              aria-label="Not helpful"
            >👎</button>
            {rated && <span className="feedback-thanks">Thanks for the feedback</span>}
          </div>
        )}
      </div>
    </div>
  );
}
