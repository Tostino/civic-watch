/**
 * Where the tool endpoint can be added, and what each client needs to be
 * handed to take it.
 *
 * The address is one string and every client wants it in a different wrapper:
 * two of them accept a URL scheme the browser can hand straight to the app,
 * one wants a command line, and the rest want the bare address typed into a
 * settings pane. So this is a list rather than a component - the pages differ
 * on how much of it they show, not on what it says.
 *
 * ORIGIN IS PASSED IN, never read here. `siteUrl()` reads a server-only
 * environment variable, and one of the two callers is a client component; a
 * helper that reached for it directly would render the deployed host on the
 * server and localhost in the browser, which is a hydration mismatch that
 * looks like a copy bug. The server decides the origin once and hands it
 * down.
 */

/** The name the server answers to (web/mcp_server.py `NAME`). A client shows
 *  it in its own list of servers, so it should match what the handshake says
 *  rather than being a second, prettier name for the same thing. */
export const MCP_NAME = "pasco-meeting-archive";

export type Install = {
  /** Stable handle. /ask shows three of these and not the fourth, and it has
   *  to select them by something other than the label a copy pass may change. */
  id: "vscode" | "cursor" | "claude-code" | "manual";
  /** What the reader calls the program, not what its vendor calls itself. */
  client: string;
  /** `link` opens the client and it confirms. `copy` puts a line on the
   *  clipboard for the reader to run or paste. `manual` is a settings pane
   *  and a sentence about where it is. */
  kind: "link" | "copy" | "manual";
  /** For `link`. */
  href?: string;
  /** For `copy`: the exact string that lands on the clipboard. */
  value?: string;
  /** What actually happens next, in one sentence. Read out on /about, held
   *  back on /ask where the label is the whole affordance. */
  note: string;
};

export const mcpUrl = (origin: string) => `${origin}/mcp`;

/** The Claude Code one-liner. No deeplink exists for it, and this is the
 *  documented form for a remote server over HTTP. */
export const claudeCodeCommand = (origin: string) =>
  `claude mcp add --transport http ${MCP_NAME} ${mcpUrl(origin)}`;

/** Base64 in whichever runtime this is. `btoa` is global in Node 16+ and in
 *  every browser; the fallback is there for the older Node a build machine
 *  might still be on. Safe for this input either way: an https URL and a
 *  hyphenated name are ASCII. */
function b64(s: string): string {
  return typeof btoa === "function"
    ? btoa(s)
    : Buffer.from(s, "utf8").toString("base64");
}

/**
 * VS Code takes the whole server object url-encoded after `vscode:mcp/install`.
 * Cursor takes the name as a query parameter and the config alone, base64'd.
 * Both then show the reader what they are about to add and wait to be told
 * yes, which is why it is honest to call these one click: nothing is written
 * behind the reader's back.
 */
export function installs(origin: string): Install[] {
  const url = mcpUrl(origin);
  const vscode =
    "vscode:mcp/install?" +
    encodeURIComponent(JSON.stringify({ name: MCP_NAME, type: "http", url }));
  const cursor =
    "cursor://anysphere.cursor-deeplink/mcp/install?name=" +
    encodeURIComponent(MCP_NAME) +
    "&config=" +
    encodeURIComponent(b64(JSON.stringify({ url })));

  return [
    {
      id: "vscode",
      client: "VS Code",
      kind: "link",
      href: vscode,
      note: "Opens VS Code, which shows you the server and asks before it saves it.",
    },
    {
      id: "cursor",
      client: "Cursor",
      kind: "link",
      href: cursor,
      note: "Opens Cursor, which shows you the server and asks before it saves it.",
    },
    {
      id: "claude-code",
      client: "Claude Code",
      kind: "copy",
      value: claudeCodeCommand(origin),
      note: "Copies one command. Run it in a terminal, in any directory.",
    },
    {
      id: "manual",
      client: "Claude and ChatGPT",
      kind: "manual",
      note:
        "Paste the address into the connector settings. In Claude it is Settings, " +
        "Connectors, Add custom connector; in ChatGPT it is Settings, Connectors. " +
        "Both of them keep custom connectors on the paid plans, which is their limit " +
        "and not ours. Every other client that speaks MCP takes the address the same way.",
    },
  ];
}
