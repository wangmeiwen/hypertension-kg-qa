import type { ReactNode } from "react";

const INLINE_TOKEN_REGEX = /(\*\*[^*]+\*\*|`[^`]+`|\[[^\]]+\]\((?:https?:\/\/|mailto:)[^)]+\))/g;
const MEDICAL_DISCLAIMER_TEXTS = [
  "以上内容仅供参考，不构成诊断或治疗建议，不能替代专业医生意见；如有不适或紧急情况，请及时就医。",
  "回答仅供信息参考，不能替代临床诊断或紧急医疗处置。",
  "The response is informational only and should not replace clinical diagnosis or urgent medical care.",
];

const MEDICAL_DISCLAIMER_PATTERNS = [
  /仅供参考/,
  /不构成诊断/,
  /不能替代专业医生意见/,
  /请及时就医/,
  /不能替代临床诊断/,
  /紧急医疗处置/,
  /informational only/i,
  /should not replace clinical diagnosis/i,
  /urgent medical care/i,
];

const isMarkdownTableSeparator = (line: string): boolean => {
  const trimmed = String(line || "").trim();
  if (!trimmed || !trimmed.includes("|")) return false;
  const cleaned = trimmed.replace(/^\|/, "").replace(/\|$/, "").trim();
  if (!cleaned) return false;
  const columns = cleaned.split("|").map((part) => part.trim());
  if (!columns.length) return false;
  return columns.every((part) => /^:?-{3,}:?$/.test(part));
};

const parseMarkdownTableRow = (line: string): string[] => {
  const cleaned = String(line || "").trim().replace(/^\|/, "").replace(/\|$/, "");
  return cleaned.split("|").map((cell) => cell.trim());
};

const renderInlineMarkdown = (text: string, keyPrefix: string): ReactNode[] | string => {
  const safeText = String(text || "");
  if (!safeText) return "";

  return safeText.split(INLINE_TOKEN_REGEX).map((part, index) => {
    if (!part) return null;

    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return <strong key={`${keyPrefix}-strong-${index}`}>{part.slice(2, -2)}</strong>;
    }

    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) {
      return <code key={`${keyPrefix}-code-${index}`}>{part.slice(1, -1)}</code>;
    }

    const linkMatch = part.match(/^\[([^\]]+)\]\(((?:https?:\/\/|mailto:)[^)]+)\)$/);
    if (linkMatch) {
      const [, label, href] = linkMatch;
      return (
        <a key={`${keyPrefix}-link-${index}`} href={href} target="_blank" rel="noreferrer">
          {label}
        </a>
      );
    }

    return <span key={`${keyPrefix}-text-${index}`}>{part}</span>;
  });
};

const getDisclaimerBlockClass = (text: string): string => {
  const normalized = String(text || "").trim();
  if (!normalized) return "";

  const isKnownDisclaimer = MEDICAL_DISCLAIMER_TEXTS.includes(normalized);
  const matchesPattern = MEDICAL_DISCLAIMER_PATTERNS.some((pattern) => pattern.test(normalized));
  return isKnownDisclaimer || matchesPattern ? " medical-disclaimer-block" : "";
};

export const renderMarkdownContent = (content: string): ReactNode[] | string => {
  const text = String(content || "").replace(/\r\n/g, "\n");
  if (!text.trim()) return "Processing...";

  const lines = text.split("\n");
  const blocks: ReactNode[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (!line.trim()) {
      i += 1;
      continue;
    }

    const fenceMatch = line.match(/^```\s*([^`]*)\s*$/);
    if (fenceMatch) {
      const lang = (fenceMatch[1] || "").trim();
      i += 1;
      const codeLines: string[] = [];
      while (i < lines.length && !/^```\s*$/.test(lines[i])) {
        codeLines.push(lines[i]);
        i += 1;
      }
      if (i < lines.length && /^```\s*$/.test(lines[i])) {
        i += 1;
      }
      blocks.push(
        <pre key={`code-${blocks.length}`} className="md-code-block">
          {lang ? <div className="md-code-lang">{lang}</div> : null}
          <code>{codeLines.join("\n")}</code>
        </pre>
      );
      continue;
    }

    const headingMatch = line.match(/^(#{1,6})\s+(.+)$/);
    if (headingMatch) {
      const level = headingMatch[1].length;
      const headingText = headingMatch[2].trim();
      const headingClass = `md-h${level}`;
      blocks.push(
        <div key={`h-${blocks.length}`} className={headingClass}>
          {renderInlineMarkdown(headingText, `h-${blocks.length}`)}
        </div>
      );
      i += 1;
      continue;
    }

    if (/^>\s?/.test(line)) {
      const quoteLines: string[] = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        quoteLines.push(lines[i].replace(/^>\s?/, ""));
        i += 1;
      }
      const quoteText = quoteLines.join(" ");
      blocks.push(
        <blockquote key={`quote-${blocks.length}`} className="md-quote">
          {renderInlineMarkdown(quoteText, `quote-${blocks.length}`)}
        </blockquote>
      );
      continue;
    }

    if (/^[-*+]\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^[-*+]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^[-*+]\s+/, "").trim());
        i += 1;
      }
      blocks.push(
        <ul key={`ul-${blocks.length}`} className="md-list">
          {items.map((item, idx) => (
            <li key={`ul-item-${idx}`}>{renderInlineMarkdown(item, `ul-${blocks.length}-${idx}`)}</li>
          ))}
        </ul>
      );
      continue;
    }

    if (/^\d+\.\s+/.test(line)) {
      const items: string[] = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\d+\.\s+/, "").trim());
        i += 1;
      }
      blocks.push(
        <ol key={`ol-${blocks.length}`} className="md-list md-ordered-list">
          {items.map((item, idx) => (
            <li key={`ol-item-${idx}`}>{renderInlineMarkdown(item, `ol-${blocks.length}-${idx}`)}</li>
          ))}
        </ol>
      );
      continue;
    }

    if (line.includes("|") && i + 1 < lines.length && isMarkdownTableSeparator(lines[i + 1])) {
      const headerCells = parseMarkdownTableRow(line);
      i += 2;

      const bodyRows: string[][] = [];
      while (i < lines.length && lines[i].trim() && lines[i].includes("|")) {
        bodyRows.push(parseMarkdownTableRow(lines[i]));
        i += 1;
      }

      blocks.push(
        <div key={`table-${blocks.length}`} className="md-table-wrap">
          <table className="md-table">
            <thead>
              <tr>
                {headerCells.map((cell, idx) => (
                  <th key={`th-${idx}`}>{renderInlineMarkdown(cell, `th-${blocks.length}-${idx}`)}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {bodyRows.map((row, rowIdx) => (
                <tr key={`tr-${rowIdx}`}>
                  {headerCells.map((_, colIdx) => (
                    <td key={`td-${rowIdx}-${colIdx}`}>
                      {renderInlineMarkdown(row[colIdx] || "", `td-${blocks.length}-${rowIdx}-${colIdx}`)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      continue;
    }

    const paragraphLines: string[] = [];
    while (
      i < lines.length
      && lines[i].trim()
      && !/^```\s*([^`]*)\s*$/.test(lines[i])
      && !/^(#{1,6})\s+/.test(lines[i])
      && !/^>\s?/.test(lines[i])
      && !/^[-*+]\s+/.test(lines[i])
      && !/^\d+\.\s+/.test(lines[i])
    ) {
      paragraphLines.push(lines[i].trim());
      i += 1;
    }

    const paragraphText = paragraphLines.join(" ");
    blocks.push(
      <p key={`p-${blocks.length}`} className={`md-paragraph${getDisclaimerBlockClass(paragraphText)}`}>
        {renderInlineMarkdown(paragraphText, `p-${blocks.length}`)}
      </p>
    );
  }

  return blocks;
};
