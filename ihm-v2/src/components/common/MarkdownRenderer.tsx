/**
 * Rendu Markdown avec coloration syntaxique pour les réponses du moteur.
 *
 * - react-markdown + remark-gfm (tables, listes de tâches, liens)
 * - rehype-highlight + highlight.js subset (yaml, python, bash, json, cpp, diff)
 * - Styles dark inline cohérents avec la palette Tailwind slate
 */
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { useEffect, useState, useRef } from "react";

// Importe uniquement les langages utiles pour la domotique (evite +300 kB)
import "highlight.js/lib/languages/yaml";
import "highlight.js/lib/languages/python";
import "highlight.js/lib/languages/bash";
import "highlight.js/lib/languages/json";
import "highlight.js/lib/languages/cpp";
import "highlight.js/lib/languages/diff";
import "highlight.js/lib/languages/plaintext";

// Injecte le thème highlight.js dark une seule fois via un style global
const HLJS_CSS = `
.hljs { background: transparent; color: #e2e8f0; }
.hljs-comment, .hljs-quote { color: #64748b; font-style: italic; }
.hljs-keyword, .hljs-selector-tag { color: #7dd3fc; }
.hljs-string, .hljs-attr { color: #86efac; }
.hljs-number, .hljs-literal { color: #fbbf24; }
.hljs-title, .hljs-section { color: #c4b5fd; font-weight: 600; }
.hljs-type, .hljs-built_in { color: #f0abfc; }
.hljs-variable { color: #fb923c; }
.hljs-deletion { color: #fca5a5; background: #450a0a40; }
.hljs-addition { color: #86efac; background: #052e1640; }
`;

let _injected = false;
function injectStyles() {
  if (_injected || typeof document === "undefined") return;
  const el = document.createElement("style");
  el.textContent = HLJS_CSS;
  document.head.appendChild(el);
  _injected = true;
}

// ─── Bloc de code avec bouton copier ─────────────────────────────────────────

function CodeBlock({ children, ...props }: React.HTMLAttributes<HTMLPreElement>) {
  const [copied, setCopied] = useState(false);
  const preRef = useRef<HTMLPreElement>(null);

  const handleCopy = () => {
    const text = preRef.current?.innerText ?? "";
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    });
  };

  return (
    <div className="group relative my-2">
      <pre
        ref={preRef}
        {...props}
        className="overflow-x-auto rounded-lg bg-slate-900/80 px-4 py-3 text-[13px] leading-relaxed"
      >
        {children}
      </pre>
      <button
        onClick={handleCopy}
        className="absolute right-2 top-2 hidden rounded bg-slate-700/80 px-2 py-0.5 text-[11px] text-slate-400 transition hover:bg-slate-600 hover:text-slate-200 group-hover:flex"
        title="Copier"
      >
        {copied ? "✓ Copié" : "Copier"}
      </button>
    </div>
  );
}

// ─── Renderer principal ───────────────────────────────────────────────────────

interface Props {
  content: string;
  className?: string;
}

export function MarkdownRenderer({ content, className = "" }: Props) {
  useEffect(() => { injectStyles(); }, []);

  return (
    <div className={`prose-chat ${className}`}>
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      rehypePlugins={[rehypeHighlight]}
      components={{
        // Blocs de code avec bouton copier
        pre({ children, ...props }) {
          return <CodeBlock {...props}>{children}</CodeBlock>;
        },
        // Code inline
        code({ children, className: cls, ...props }) {
          const isBlock = cls?.startsWith("language-");
          if (isBlock) {
            return <code className={cls} {...props}>{children}</code>;
          }
          return (
            <code
              {...props}
              className="rounded bg-slate-800 px-1.5 py-0.5 text-[12px] text-emerald-300"
            >
              {children}
            </code>
          );
        },
        // Titres
        h1: ({ children }) => <h1 className="mb-2 mt-3 text-base font-bold text-slate-100">{children}</h1>,
        h2: ({ children }) => <h2 className="mb-1.5 mt-2.5 text-sm font-bold text-slate-200">{children}</h2>,
        h3: ({ children }) => <h3 className="mb-1 mt-2 text-sm font-semibold text-slate-300">{children}</h3>,
        // Listes
        ul: ({ children }) => <ul className="my-1.5 list-disc pl-5 text-slate-200">{children}</ul>,
        ol: ({ children }) => <ol className="my-1.5 list-decimal pl-5 text-slate-200">{children}</ol>,
        li: ({ children }) => <li className="my-0.5 text-sm">{children}</li>,
        // Paragraphe
        p: ({ children }) => <p className="my-1.5 text-sm leading-relaxed">{children}</p>,
        // Liens
        a: ({ href, children }) => (
          <a href={href} target="_blank" rel="noopener noreferrer" className="text-sky-400 underline hover:text-sky-300">
            {children}
          </a>
        ),
        // Séparateur
        hr: () => <hr className="my-3 border-slate-700" />,
        // Blockquote
        blockquote: ({ children }) => (
          <blockquote className="my-2 border-l-2 border-sky-600/60 pl-3 text-slate-400 italic">
            {children}
          </blockquote>
        ),
        // Tableaux
        table: ({ children }) => (
          <div className="my-2 overflow-x-auto">
            <table className="w-full border-collapse text-sm">{children}</table>
          </div>
        ),
        th: ({ children }) => (
          <th className="border border-slate-700 bg-slate-800/60 px-3 py-1.5 text-left text-xs font-semibold text-slate-300">
            {children}
          </th>
        ),
        td: ({ children }) => (
          <td className="border border-slate-700 px-3 py-1.5 text-xs text-slate-300">{children}</td>
        ),
        // Strong / em
        strong: ({ children }) => <strong className="font-semibold text-slate-100">{children}</strong>,
        em: ({ children }) => <em className="text-slate-300">{children}</em>,
      }}
    >
      {content}
    </ReactMarkdown>
    </div>
  );
}
