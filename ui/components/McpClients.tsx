import { CopyButton } from "./CopyButton";
import { clients, mcpUrl, type Snippet } from "@/lib/mcp";
import s from "./McpClients.module.css";

/**
 * The five ways in, one at a time.
 *
 * NO JAVASCRIPT. The tabs are a radio group and the panels are shown by
 * `:checked ~`, which costs five pairs of CSS rules and buys three things
 * worth more than the brevity: it works before React hydrates, it works if
 * React never hydrates, and the arrow keys already move between the tabs
 * because a radio group is what this actually is. The only script on the page
 * is the clipboard, which cannot be anything else.
*/
export function McpClients({ origin, name }: { origin: string; name: string }) {
  const list = clients(origin, name);
  const url = mcpUrl(origin);

  return (
    <fieldset className={s.wrap}>
      <legend className={s.vh}>Choose your assistant</legend>

      {/* Every input before every label, so `#id:checked ~ .tabs label` and
          `#id:checked ~ .panels [data-client]` both reach their target with a
          sibling combinator and the file needs no :has(). */}
      {list.map((c, i) => (
        <input
          key={c.id}
          type="radio"
          name="mcp-client"
          id={`mcpc-${c.id}`}
          className={s.radio}
          defaultChecked={i === 0}
        />
      ))}

      <div className={s.tabs}>
        {list.map((c) => (
          <label key={c.id} htmlFor={`mcpc-${c.id}`} className={s.tab}>
            {c.name}
          </label>
        ))}
      </div>

      <div className={s.panels}>
        {list.map((c) => (
          <div key={c.id} className={s.panel} data-client={c.id}>
            <p className={s.lede}>{c.lede}</p>

            {/* Before the steps, not after them: the reader is about to be
                told to paste, and the clipboard should already hold the thing
                by the time they get there. */}
            {c.wantsAddress ? (
              <CopyButton
                className={s.address}
                value={url}
                label="Copy the address"
                done="Address copied"
              />
            ) : null}

            {c.snippet ? <Block snippet={c.snippet} /> : null}

            {c.steps ? (
              <ol className={s.steps}>
                {c.steps.map((step) => (
                  <li key={step}>{step}</li>
                ))}
              </ol>
            ) : null}

            {c.also ? (
              <>
                <p className={s.alsoLead}>{c.also.lead}</p>
                <Block snippet={c.also} quiet />
              </>
            ) : null}

            {c.note ? <p className={s.note}>{c.note}</p> : null}
          </div>
        ))}
      </div>
    </fieldset>
  );
}

/* A thing to copy whole. The language sits in the corner because a reader who
 * has landed on the Codex tab needs to know which of the two blocks is the
 * command and which is the file, and the two are one line each. */
function Block({ snippet, quiet }: { snippet: Snippet; quiet?: boolean }) {
  return (
    <div className={s.block} data-quiet={quiet || undefined}>
      <div className={s.blockHead}>
        <span className={s.lang}>{snippet.lang}</span>
        <CopyButton
          className={s.blockCopy}
          value={snippet.text}
          label={snippet.label}
          done="Copied"
        />
      </div>
      <pre className={s.code}>
        <code>{snippet.text}</code>
      </pre>
    </div>
  );
}
