"use client";

interface CodeViewerProps {
  path: string;
  startLine?: number;
  endLine?: number;
  content?: string | null;
  reason?: string;
}

/**
 * Lightweight read-only code viewer with line numbers and basic token
 * highlighting. Only ever displays chunks attached to an investigation —
 * there is no arbitrary repository browsing.
 */
export function CodeSnippet({ path, startLine = 1, endLine, content }: CodeViewerProps) {
  if (!content) {
    return (
      <div className="rounded-md border border-slate-200 bg-slate-50 p-3 text-sm text-slate-500">
        {path}
        {startLine ? `:${startLine}` : ""} — snippet not stored
      </div>
    );
  }
  const lines = content.split("\n");
  const width = String(startLine + lines.length - 1).length;

  return (
    <div className="overflow-hidden rounded-md border border-slate-200">
      <div className="border-b border-slate-100 bg-slate-50 px-4 py-2 font-mono text-xs text-slate-500">
        {path}
        {endLine && endLine !== startLine ? `:${startLine}-${endLine}` : `:${startLine}`}
      </div>
      <pre className="overflow-x-auto bg-slate-900 p-0 text-xs leading-5">
        <code className="block text-slate-100">
          {lines.map((line, index) => (
            <span key={index} className="flex hover:bg-slate-800">
              <span
                className="inline-block shrink-0 select-none border-r border-slate-700 bg-slate-800/60 px-3 text-right text-slate-400"
                style={{ minWidth: `${width + 1.5}ch` }}
              >
                {startLine + index}
              </span>
              <span className="whitespace-pre px-4">{line || " "}</span>
            </span>
          ))}
        </code>
      </pre>
    </div>
  );
}

const KEYWORDS =
  /\b(def|end|class|module|function|return|if|elsif|else|unless|while|for|in|do|begin|rescue|ensure|raise|nil|true|false|self|require|yield|import|from|async|await|const|let|var|type|struct|func|package|public|private|protected|new|try|catch|finally|throw)\b/g;

/** Minimal highlighter for short snippets (keywords, strings, comments). */
export function HighlightedLine({ line }: { line: string }) {
  const escaped = line.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  const html = escaped
    .replace(/(#.*$)/g, '<span class="text-slate-400">$1</span>')
    .replace(/(&quot;|"[^"]*")/g, '<span class="text-emerald-300">$1</span>')
    .replace(KEYWORDS, '<span class="text-violet-300">$&</span>');
  return <span dangerouslySetInnerHTML={{ __html: html }} />;
}
