import { useEffect, useRef, useState } from "react";
import type { ReactElement } from "react";
import {
  BookOpen,
  Brain,
  FileEdit,
  FilePen,
  Hammer,
  Loader2,
  Play,
  SquareTerminal,
} from "lucide-react";

import { MarkdownRenderer } from "@/components/conversation/markdown-renderer.tsx";
import {
  extractBashCommand,
  extractToolPath,
  extractToolStringArg,
  formatToolOutput,
  parseEditArgsPartial,
  renderSimpleDiff,
  stripAnsi,
  type DiffLine,
} from "@/shared/lib/tool-renderers";
import type { RenderBlock } from "../lib/conversation-transcript";
import { toolDisplay } from "../lib/conversation-transcript";
import { parseJsonMaybe } from "../lib/conversation-transcript";
import type { AskUserAnswerInput } from "@/shared/lib/api";

type ToolRenderer = (block: RenderBlock) => ReactElement;
const TOOL_PREVIEW_CHARS = 16_000;

function BoundedPre({ value, className = "msg__tool-pre" }: { value: string; className?: string }) {
  const [expanded, setExpanded] = useState(false);
  const truncated = value.length > TOOL_PREVIEW_CHARS;
  const shown = truncated && !expanded
    ? `${value.slice(0, TOOL_PREVIEW_CHARS)}\n\n… ${value.length - TOOL_PREVIEW_CHARS} more characters`
    : value;
  return (
    <div className="msg__bounded-output">
      <pre className={className}>{shown}</pre>
      {truncated && (
        <button className="button" type="button" onClick={() => setExpanded((current) => !current)}>
          {expanded ? "Collapse" : "Show full output"}
        </button>
      )}
    </div>
  );
}

interface AskUserQuestionView {
  id: string;
  header: string;
  question: string;
  options: Array<{ label: string; description: string }>;
}

function askUserQuestions(args: string | undefined): AskUserQuestionView[] {
  const parsed = parseJsonMaybe(args ?? "{}");
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return [];
  const questions = (parsed as Record<string, unknown>).questions;
  if (!Array.isArray(questions)) return [];
  return questions.flatMap((value) => {
    if (!value || typeof value !== "object" || Array.isArray(value)) return [];
    const item = value as Record<string, unknown>;
    const id = typeof item.id === "string" ? item.id : "";
    const question = typeof item.question === "string" ? item.question : "";
    if (!id || !question) return [];
    const options = Array.isArray(item.options) ? item.options.flatMap((option) => {
      if (!option || typeof option !== "object" || Array.isArray(option)) return [];
      const record = option as Record<string, unknown>;
      if (typeof record.label !== "string" || !record.label) return [];
      return [{
        label: record.label,
        description: typeof record.description === "string" ? record.description : "",
      }];
    }) : [];
    return [{
      id,
      question,
      header: typeof item.header === "string" ? item.header : "",
      options,
    }];
  });
}

function AskUserRenderer({
  block,
  onAnswerQuestion,
}: {
  block: RenderBlock;
  onAnswerQuestion: (toolCallId: string, answers: AskUserAnswerInput[], skipped?: boolean) => boolean;
}): ReactElement {
  const questions = askUserQuestions(block.toolArgs);
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const completed = block.toolStatus === "completed";
  const canSubmit = questions.length > 0 && questions.every((question) => answers[question.id]?.trim());

  if (completed) {
    const result = parseJsonMaybe(block.toolResult ?? "");
    const skipped = Boolean(result && typeof result === "object" && !Array.isArray(result)
      && (result as Record<string, unknown>).status === "skipped");
    return (
      <div className="msg__ask-user msg__ask-user--completed">
        <div className="msg__ask-user-title">{skipped ? "Questions skipped" : "Questions answered"}</div>
        {!skipped && <pre className="msg__tool-pre">{block.toolResult}</pre>}
      </div>
    );
  }

  return (
    <div className="msg__ask-user">
      <div className="msg__ask-user-title">The assistant needs your input</div>
      {questions.map((question) => (
        <fieldset key={question.id} className="msg__ask-question" disabled={submitting}>
          {question.header && <legend>{question.header}</legend>}
          <label htmlFor={`ask-${block.toolCallId}-${question.id}`}>{question.question}</label>
          {question.options.length > 0 && (
            <div className="msg__ask-options">
              {question.options.map((option) => (
                <button
                  key={option.label}
                  type="button"
                  className={answers[question.id] === option.label ? "button is-selected" : "button"}
                  title={option.description}
                  onClick={() => setAnswers((current) => ({ ...current, [question.id]: option.label }))}
                >
                  {option.label}
                </button>
              ))}
            </div>
          )}
          <input
            id={`ask-${block.toolCallId}-${question.id}`}
            className="input"
            value={answers[question.id] ?? ""}
            placeholder="Type your answer"
            onChange={(event) => setAnswers((current) => ({ ...current, [question.id]: event.target.value }))}
          />
        </fieldset>
      ))}
      <div className="msg__ask-actions">
        <button
          type="button"
          className="button"
          disabled={submitting}
          onClick={() => {
            if (!block.toolCallId || !onAnswerQuestion(block.toolCallId, [], true)) return;
            setSubmitting(true);
          }}
        >Skip</button>
        <button
          type="button"
          className="button button--primary"
          disabled={submitting || !canSubmit}
          onClick={() => {
            if (!block.toolCallId) return;
            const values = questions.map((question) => ({ id: question.id, answer: answers[question.id].trim() }));
            if (onAnswerQuestion(block.toolCallId, values)) setSubmitting(true);
          }}
        >{submitting ? "Submitting…" : "Submit answers"}</button>
      </div>
    </div>
  );
}

function pythonHighlightedSegments(line: string): Array<{ text: string; kind: string }> {
  const commentIndex = line.indexOf("#");
  const code = commentIndex >= 0 ? line.slice(0, commentIndex) : line;
  const comment = commentIndex >= 0 ? line.slice(commentIndex) : "";
  const pattern = /("(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|\b(?:False|None|True|and|as|assert|async|await|break|class|continue|def|del|elif|else|except|finally|for|from|global|if|import|in|is|lambda|nonlocal|not|or|pass|raise|return|try|while|with|yield)\b|\b\d+(?:\.\d+)?\b)/g;
  const segments: Array<{ text: string; kind: string }> = [];
  let cursor = 0;
  for (const match of code.matchAll(pattern)) {
    const index = match.index ?? 0;
    if (index > cursor) {
      segments.push({ text: code.slice(cursor, index), kind: "plain" });
    }
    const text = match[0];
    const kind = text.startsWith("\"") || text.startsWith("'")
      ? "string"
      : /^\d/.test(text)
        ? "number"
        : "keyword";
    segments.push({ text, kind });
    cursor = index + text.length;
  }
  if (cursor < code.length) {
    segments.push({ text: code.slice(cursor), kind: "plain" });
  }
  if (comment) {
    segments.push({ text: comment, kind: "comment" });
  }
  return segments;
}

function PythonCodeBlock({ code }: { code: string }) {
  return (
    <pre className="msg__python">
      <code>
        {code.split("\n").map((line, index) => (
          <span key={index} className="msg__python-line">
            {pythonHighlightedSegments(line).map((segment, segmentIndex) => (
              <span key={segmentIndex} className={`msg__python-${segment.kind}`}>
                {segment.text}
              </span>
            ))}
          </span>
        ))}
      </code>
    </pre>
  );
}

function pendingToolLabel(toolName: string | undefined): string {
  const name = toolName ?? "";
  if (name === "bash") return "Running bash...";
  if (name === "execute_python" || name.endsWith(".execute_python")) return "Running python...";
  if (name === "read") return "Reading...";
  if (name === "edit") return "Editing...";
  if (name === "write") return "Writing...";
  if (name.startsWith("yext.")) return `Running ${name}...`;
  return "Running...";
}

function pendingToolIcon(toolName: string | undefined) {
  const name = toolName ?? "";
  if (name === "read") return BookOpen;
  if (name === "edit") return FileEdit;
  if (name === "write") return FilePen;
  if (name === "bash" || name === "execute_python" || name.endsWith(".execute_python")) return Play;
  return Hammer;
}

function PendingToolBanner({ toolName }: { toolName: string | undefined }) {
  const Icon = pendingToolIcon(toolName);
  const label = pendingToolLabel(toolName);
  return (
    <div className="msg__tool-pending">
      <Icon size={14} />
      <span>{label}</span>
      <Loader2 size={14} className="msg__tool-spinner" />
    </div>
  );
}

function PendingToolShell({ toolName }: { toolName: string | undefined }) {
  return (
    <div className="msg__tool">
      <div className="msg__tool-head">
        <Hammer size={14} />
        <span>{toolName ?? "tool"}</span>
        <Loader2 size={14} className="msg__tool-spinner" />
      </div>
      <PendingToolBanner toolName={toolName} />
    </div>
  );
}

function isToolRunning(block: RenderBlock): boolean {
  if (block.toolStatus === "running") return true;
  if (block.toolStatus === "completed") return false;
  return !block.toolResult;
}

function ThinkingBlock({ content, isStreaming }: { content: string; isStreaming: boolean }) {
  const scrollRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!isStreaming) return;
    const node = scrollRef.current;
    if (node) {
      node.scrollTop = node.scrollHeight;
    }
  }, [content, isStreaming]);

  return (
    <details open className="msg__thinking">
      <summary className="msg__thinking-summary">
        <Brain size={14} />
        <span>thinking</span>
        {isStreaming && <Loader2 size={12} className="msg__tool-spinner" />}
        <span className="msg__thinking-toggle msg__thinking-toggle--closed">expand</span>
        <span className="msg__thinking-toggle msg__thinking-toggle--open">collapse</span>
      </summary>
      <div ref={scrollRef} className="msg__thinking-body">
        {content}
      </div>
    </details>
  );
}

function diffLineClass(kind: DiffLine["kind"]): string {
  if (kind === "add") return "msg__diff-add";
  if (kind === "del") return "msg__diff-del";
  return "msg__diff-context";
}

function diffLinePrefix(kind: DiffLine["kind"]): string {
  if (kind === "add") return "+";
  if (kind === "del") return "-";
  return " ";
}

function BashRenderer(block: RenderBlock): ReactElement {
  const display = toolDisplay(block);
  const isRunning = isToolRunning(block);
  const streamedCommand = extractToolStringArg(block.toolArgs ?? "", "command");
  const command = streamedCommand ?? (block.toolArgs ? extractBashCommand(block.toolArgs) : "");
  const result = block.toolResult ? formatToolOutput(block.toolResult) : null;
  return (
    <div className="msg__tool">
      <div className="msg__tool-head">
        <Hammer size={14} />
        <span>bash</span>
        {isRunning && <Loader2 size={14} className="msg__tool-spinner" />}
        {block.toolStatus && !isRunning && (
          <span className="msg__tool-status">{block.toolStatus}</span>
        )}
      </div>
      <div className="msg__tool-grid">
        <div className="msg__tool-panel msg__tool-panel--call">
          <div className="msg__tool-panel-head">
            <SquareTerminal size={14} />
            <span>command</span>
          </div>
          <BoundedPre value={command} />
        </div>
        <div className="msg__tool-panel msg__tool-panel--result">
          <div className="msg__tool-panel-head">
            <SquareTerminal size={14} />
            <span>result</span>
          </div>
          {result === null
            ? <PendingToolBanner toolName={display.name} />
            : <BoundedPre value={result} />}
        </div>
      </div>
    </div>
  );
}

function EditRenderer(block: RenderBlock): ReactElement {
  const args = parseEditArgsPartial(block.toolArgs ?? "");
  const diff = renderSimpleDiff(args.old_string, args.new_string);
  const isRunning = isToolRunning(block);
  return (
    <div className="msg__tool">
      <div className="msg__tool-head">
        <Hammer size={14} />
        <span>edit</span>
        {isRunning && <Loader2 size={14} className="msg__tool-spinner" />}
        {block.toolStatus && !isRunning && (
          <span className="msg__tool-status">{block.toolStatus}</span>
        )}
      </div>
      {args.path && <div className="msg__tool-path" title={args.path}>{args.path}</div>}
      {isRunning && <PendingToolBanner toolName="edit" />}
      <pre className="msg__diff">
        <code>
          {diff.map((line, index) => (
            <span key={index} className={`msg__diff-line ${diffLineClass(line.kind)}`}>
              {diffLinePrefix(line.kind)}
              {line.text}
            </span>
          ))}
        </code>
      </pre>
    </div>
  );
}

function ReadRenderer(block: RenderBlock): ReactElement {
  const path = extractToolPath(block.toolArgs ?? "") ?? "";
  const isRunning = isToolRunning(block);
  const result = isRunning ? null : stripAnsi(block.toolResult ?? "");
  return (
    <div className="msg__tool">
      <div className="msg__tool-head">
        <Hammer size={14} />
        <span>read</span>
        {isRunning && <Loader2 size={14} className="msg__tool-spinner" />}
        {block.toolStatus && !isRunning && (
          <span className="msg__tool-status">{block.toolStatus}</span>
        )}
      </div>
      {path && <div className="msg__tool-path" title={path}>{path}</div>}
      {isRunning
        ? <PendingToolBanner toolName="read" />
        : <BoundedPre className="msg__tool-output" value={result ?? ""} />}
    </div>
  );
}

function WriteRenderer(block: RenderBlock): ReactElement {
  const path = extractToolPath(block.toolArgs ?? "") ?? "";
  const content = extractToolStringArg(block.toolArgs ?? "", "content") ?? "";
  const isRunning = isToolRunning(block);
  const result = isRunning ? null : stripAnsi(block.toolResult ?? "");
  return (
    <div className="msg__tool">
      <div className="msg__tool-head">
        <Hammer size={14} />
        <span>write</span>
        {isRunning && <Loader2 size={14} className="msg__tool-spinner" />}
        {block.toolStatus && !isRunning && (
          <span className="msg__tool-status">{block.toolStatus}</span>
        )}
      </div>
      {path && <div className="msg__tool-path" title={path}>{path}</div>}
      <BoundedPre value={content} />
      {isRunning
        ? <PendingToolBanner toolName="write" />
        : <BoundedPre className="msg__tool-output" value={result ?? ""} />}
    </div>
  );
}

const toolRendererRegistry: Record<string, ToolRenderer> = {
  bash: BashRenderer,
  edit: EditRenderer,
  read: ReadRenderer,
  write: WriteRenderer,
};

export function MessageBlockView({
  actorId,
  block,
  isStreaming,
  onAnswerQuestion,
}: {
  actorId: string;
  block: RenderBlock;
  isStreaming: boolean;
  onAnswerQuestion: (toolCallId: string, answers: AskUserAnswerInput[], skipped?: boolean) => boolean;
}) {
  if (block.type === "thinking") {
    return <ThinkingBlock content={block.content} isStreaming={isStreaming} />;
  }

  if (block.type === "tool_group") {
    const display = toolDisplay(block);
    if (display.name === "ask_user") {
      return <AskUserRenderer block={block} onAnswerQuestion={onAnswerQuestion} />;
    }
    const isExecutePython = display.name === "execute_python" || display.name.endsWith(".execute_python");
    const isRunning = isToolRunning(block);

    if (isExecutePython) {
      const streamedCode = extractToolStringArg(block.toolArgs ?? "", "code");
      const code = display.code ?? streamedCode;
      if ((code === undefined || code === null) && isRunning) {
        return <PendingToolShell toolName={display.name} />;
      }
      return (
        <div className="msg__tool">
          <div className="msg__tool-head">
            <Hammer size={14} />
            <span>execute_python</span>
            {isRunning && <Loader2 size={14} className="msg__tool-spinner" />}
          </div>
          <div className="msg__tool-grid">
            <div className="msg__tool-code">
              <PythonCodeBlock code={code ?? ""} />
            </div>
            {isRunning && !block.toolResult ? (
              <div className="msg__tool-panel msg__tool-panel--result msg__tool-panel--pending">
                <PendingToolBanner toolName={display.name} />
              </div>
            ) : (
              <BoundedPre className="msg__tool-output" value={formatToolOutput(block.toolResult ?? "")} />
            )}
          </div>
        </div>
      );
    }

    const renderer = toolRendererRegistry[display.name];
    if (renderer) {
      return renderer(block);
    }

    return (
      <div className="msg__tool">
        <div className="msg__tool-head">
          <Hammer size={14} />
          <span>{display.name}</span>
          {isRunning && <Loader2 size={14} className="msg__tool-spinner" />}
          {block.toolStatus && (
            <span className="msg__tool-status">{block.toolStatus}</span>
          )}
        </div>
        <div className="msg__tool-grid">
          <div className="msg__tool-panel msg__tool-panel--call">
            <div className="msg__tool-panel-head">
              <SquareTerminal size={14} />
              <span>tool call</span>
            </div>
            <BoundedPre value={display.argsText} />
          </div>
          <div className="msg__tool-panel msg__tool-panel--result">
            <div className="msg__tool-panel-head">
              <SquareTerminal size={14} />
              <span>tool result</span>
            </div>
            {isRunning
              ? <PendingToolBanner toolName={display.name} />
              : <BoundedPre value={block.toolResult ?? "pending"} />}
          </div>
        </div>
      </div>
    );
  }

  if (block.type === "tool_call") {
    return (
      <div className="msg__tool-inline msg__tool-inline--call">
        <div className="msg__tool-inline-title">{block.content}</div>
        {block.toolArgs && <BoundedPre value={block.toolArgs} />}
      </div>
    );
  }

  if (block.type === "tool_result") {
    return <BoundedPre className="msg__tool-inline msg__tool-inline--result" value={block.content} />;
  }

  if (block.type === "error") {
    return <div className="msg__error">{block.content}</div>;
  }

  if (block.type === "raw") {
    return <pre className="msg__raw">{block.content}</pre>;
  }

  return (
    <>
      <MarkdownRenderer actorId={actorId} content={block.content} />
      {isStreaming && <span className="stream-cursor" aria-hidden="true" />}
    </>
  );
}
