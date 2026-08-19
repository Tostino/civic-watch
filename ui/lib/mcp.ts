/**
 * Where the tool endpoint can be added, and the exact thing each client wants
 * to be given.
 *
 * ORIGIN IS PASSED IN, never read here. `siteUrl()` reads a server-only
 * environment variable and one of the two callers is a client component; a
 * helper that reached for it directly would render the deployed host on the
 * server and localhost in the browser, which is a hydration mismatch that
 * presents as a copy button handing over the wrong address.
*/

/** The name the server answers to (web/mcp_server.py `NAME`). A client lists
 *  its servers under this, so it should match what the handshake says rather
 *  than being a second, prettier name for the same thing. */
export const MCP_NAME = "pasco-meeting-archive";

export const mcpUrl = (origin: string) => `${origin}/mcp`;

/** A block the reader is meant to copy whole. `lang` is for the label in the
 *  corner, not for highlighting: there is no syntax colouring here and there
 *  should not be, since two of these three are one line long. */
export type Snippet = {
  lang: "shell" | "toml" | "json";
  text: string;
  /** What the copy control says. Written per snippet because "Copy" alone is
   *  ambiguous the moment a panel holds two of them. */
  label: string;
};

export type Client = {
  id: string;
  name: string;
  /** One line under the tab, saying what this client is. */
  lede: string;
  snippet?: Snippet;
  /** The subordinate path: an older release, or the file behind the command. */
  also?: { lead: string } & Snippet;
  /** Numbered, for the clients where the work is in a settings pane. */
  steps?: string[];
  /** Whether that pane needs the address on the clipboard first. */
  wantsAddress?: boolean;
  /** The limit, or the caveat, in the reader's own interest. */
  note?: string;
};

/**
 * ORDER IS THE ARGUMENT. The two apps come first and the two terminals after,
 * because /about renders these as tabs and shows the first one by default: a
 * reader who has never opened a terminal used to arrive at `claude mcp add
 * --transport http`, which answers a question they did not ask and reads as
 * "this is not for you". "Anything else" stays last; it is the one to reach
 * for once none of the four named ones matched.
 */
export function clients(origin: string): Client[] {
  const url = mcpUrl(origin);
  return [
    {
      id: "claude",
      name: "Claude",
      lede: "In the app, on the web, or on a phone.",
      wantsAddress: true,
      steps: [
        "Open Settings, then Connectors.",
        "Choose Add custom connector.",
        "Paste the address, and save.",
      ],
      note:
        "Anthropic keeps custom connectors on the paid plans. That is their limit and not ours: " +
        "the endpoint itself asks nothing of anybody.",
    },
    {
      id: "chatgpt",
      name: "ChatGPT",
      lede: "In the app or on the web.",
      wantsAddress: true,
      steps: [
        "Open Settings, then Connectors.",
        "Add a connector, and paste the address.",
        "Turn the connector on for the conversation you want it in.",
      ],
      note:
        "OpenAI keeps custom connectors on the paid plans, and on some of them behind developer mode. " +
        "That is their limit and not ours.",
    },
    {
      id: "claude-code",
      name: "Claude Code",
      lede: "One command, in a terminal.",
      snippet: {
        lang: "shell",
        text: `claude mcp add --transport http ${MCP_NAME} ${url}`,
        label: "Copy the command",
      },
      note:
        "Run it in any directory. That saves it for the project you are in; " +
        "add --scope user to the end to have it in all of them.",
    },
    {
      id: "codex",
      name: "Codex",
      lede: "One command, in a terminal.",
      snippet: {
        lang: "shell",
        text: `codex mcp add ${MCP_NAME} --url ${url}`,
        label: "Copy the command",
      },
      also: {
        lead: "If your Codex does not take --url, write it into ~/.codex/config.toml instead:",
        lang: "toml",
        text: `[mcp_servers.${MCP_NAME}]\nurl = "${url}"`,
        label: "Copy the config",
      },
    },
    {
      id: "other",
      name: "Anything else",
      lede: "Streamable HTTP. No sign-in, no key, no account.",
      snippet: {
        lang: "json",
        text: `{\n  "${MCP_NAME}": {\n    "type": "http",\n    "url": "${url}"\n  }\n}`,
        label: "Copy the server block",
      },
      note:
        "Most clients want the address on its own and nothing else. The block above is for the ones " +
        "that keep a JSON file of their servers.",
    },
  ];
}
