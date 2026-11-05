import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import type { Citation, Report } from "@/lib/types";

/** Turn "[3]" into a markdown link the custom renderer shows as a superscript citation. */
const linkCites = (text: string) => text.replace(/\[(\d+)\](?!\()/g, "[$1](#ref-$1)");

function md(citations: Citation[]): Components {
  const byN = new Map(citations.map((c) => [String(c.n), c]));
  return {
    a({ href, children }) {
      const m = href?.match(/^#ref-(\d+)$/);
      if (m) {
        const c = byN.get(m[1]);
        return (
          <a href={href} className="cite" title={c ? c.title : undefined}>
            {m[1]}
          </a>
        );
      }
      return (
        <a href={href} target="_blank" rel="noreferrer" className="text-signal underline underline-offset-2">
          {children}
        </a>
      );
    },
  };
}

function Prose({ text, citations }: { text: string; citations: Citation[] }) {
  return (
    <ReactMarkdown remarkPlugins={[remarkGfm]} components={md(citations)}>
      {linkCites(text)}
    </ReactMarkdown>
  );
}

export function ReportView({ report, downloadHref }: { report: Report; downloadHref: string }) {
  const stats = (report.stats ?? {}) as Record<string, number>;
  const options = report.comparison_options ?? [];
  const rows = report.comparison ?? [];

  return (
    <article className="bg-paper border border-rule rounded-2xl px-5 sm:px-10 py-8 sm:py-10">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="font-serif text-[28px] sm:text-[32px] leading-tight tracking-[-0.01em] max-w-[30ch]">
          {report.title}
        </h2>
        <a href={downloadHref} download="brief.md" className="text-sm text-signal underline underline-offset-4">
          Download Markdown
        </a>
      </div>
      <p className="mt-2 text-sm text-ink-faint">
        {stats.verified ?? 0} verified findings from {report.citations.length} sources, {stats.revisions ?? 0}{" "}
        {stats.revisions === 1 ? "revision" : "revisions"}, {stats.seconds ?? "?"}s
      </p>

      <div className="prose-brief mt-6 max-w-[68ch] border-l-2 border-signal pl-5 text-[19px]">
        <Prose text={report.tldr} citations={report.citations} />
      </div>

      {options.length > 0 && rows.length > 0 && (
        <div className="mt-8 overflow-x-auto">
          <table className="w-full text-sm min-w-[480px]">
            <thead>
              <tr className="text-left text-ink-soft">
                <th className="py-2 pr-4 font-medium" />
                {options.map((o) => (
                  <th key={o} className="py-2 pr-4 font-semibold text-ink">
                    {o}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.dimension} className="border-t border-rule align-top">
                  <th scope="row" className="py-2.5 pr-4 font-medium text-ink-soft text-left">
                    {r.dimension}
                  </th>
                  {options.map((o, i) => (
                    <td key={o} className="py-2.5 pr-4 text-ink [&_p]:m-0">
                      <Prose text={r.values[i] ?? "—"} citations={report.citations} />
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="prose-brief mt-8 max-w-[68ch]">
        {report.sections.map((s) => (
          <section key={s.heading} className="mt-8 first:mt-0">
            <h3 className="font-sans text-[15px] font-semibold text-ink mb-2">{s.heading}</h3>
            <Prose text={s.body} citations={report.citations} />
          </section>
        ))}
      </div>

      {(report.caveats ?? []).length > 0 && (
        <section className="mt-8 max-w-[68ch]">
          <h3 className="text-[15px] font-semibold mb-2">What this brief can’t tell you</h3>
          <ul className="list-disc pl-5 text-ink-soft space-y-1">
            {report.caveats!.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </section>
      )}

      <section className="mt-10 border-t border-rule pt-6">
        <h3 className="text-[15px] font-semibold mb-3">Sources</h3>
        <ol className="space-y-1.5 text-sm">
          {report.citations.map((c) => (
            <li key={c.n} id={`ref-${c.n}`} className="flex gap-3 scroll-mt-6 target:bg-signal-soft rounded">
              <span className="text-ink-faint w-5 text-right shrink-0">{c.n}</span>
              <a href={c.url} target="_blank" rel="noreferrer" className="text-ink hover:text-signal break-words">
                {c.title}
                <span className="text-ink-faint"> {new URL(c.url).hostname.replace(/^www\./, "")}</span>
              </a>
            </li>
          ))}
        </ol>
      </section>
    </article>
  );
}
