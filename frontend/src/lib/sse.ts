/**
 * An incremental parser for the server-sent events wire format (WHATWG HTML, "Parsing an event
 * stream"). Fed arbitrary text chunks — a message may arrive split anywhere, even between `\r`
 * and `\n` — it returns each message once its terminating blank line has arrived.
 */

export interface SseMessage {
  /** `event:` field; "message" when absent, as in the spec. */
  event: string;
  /** `data:` lines joined with "\n". */
  data: string;
  /** `id:` field of this message, if it had one. */
  id: string | undefined;
  /** `retry:` field (milliseconds), if it had a valid one. */
  retry: number | undefined;
}

export class SseParser {
  private buffer = "";
  private dataLines: string[] = [];
  private event = "";
  private id: string | undefined;
  private retry: number | undefined;
  private hasFields = false;

  push(chunk: string): SseMessage[] {
    this.buffer += chunk;
    const messages: SseMessage[] = [];
    let start = 0;
    for (let i = 0; i < this.buffer.length; i++) {
      const ch = this.buffer[i];
      if (ch !== "\n" && ch !== "\r") continue;
      // A trailing "\r" may be the first half of "\r\n": wait for the next chunk to decide.
      if (ch === "\r" && i === this.buffer.length - 1) break;
      const line = this.buffer.slice(start, i);
      if (ch === "\r" && this.buffer[i + 1] === "\n") i++;
      start = i + 1;
      const message = this.processLine(line);
      if (message) messages.push(message);
    }
    this.buffer = this.buffer.slice(start);
    return messages;
  }

  private processLine(line: string): SseMessage | undefined {
    if (line === "") return this.dispatch();
    if (line.startsWith(":")) return undefined; // comment (keep-alive ping)
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    switch (field) {
      case "event":
        this.event = value;
        break;
      case "data":
        this.dataLines.push(value);
        break;
      case "id":
        if (!value.includes("\0")) this.id = value;
        break;
      case "retry":
        if (/^\d+$/.test(value)) this.retry = Number(value);
        break;
      default:
        return undefined; // unknown field: ignored, per the spec
    }
    this.hasFields = true;
    return undefined;
  }

  private dispatch(): SseMessage | undefined {
    const message: SseMessage | undefined = this.hasFields
      ? {
          event: this.event || "message",
          data: this.dataLines.join("\n"),
          id: this.id,
          retry: this.retry,
        }
      : undefined;
    this.dataLines = [];
    this.event = "";
    this.id = undefined;
    this.retry = undefined;
    this.hasFields = false;
    return message;
  }
}
