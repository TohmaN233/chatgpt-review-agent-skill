# Browser Workflows

Use browser-control tooling against the ChatGPT Codex side browser/tab.

## Upload A Zip Packet

Use zip attachments for multi-file review packets. ChatGPT can read the files inside an uploaded zip, so include `review-packet.md` plus the supporting source files/logs the reviewer needs.

```js
const fs = await import("node:fs/promises");
const bytes = await fs.readFile("<absolute-review-packet-zip>");
await tab.clipboard.write([
  {
    presentationStyle: "attachment",
    entries: [
      {
        mimeType: "application/zip",
        base64: Buffer.from(bytes).toString("base64"),
      },
    ],
  },
]);
await tab.playwright
  .getByRole("textbox", { name: "与 ChatGPT 聊天" })
  .press("ControlOrMeta+V");
```

After pasting, confirm the DOM shows the attached `.zip` chip before sending.

## Capture The Review

Capture only the newest assistant message after the latest packet prompt.

Prefer clicking the copy button on that newest assistant response, then read
the clipboard text and save it as a local Markdown file at the requested handoff
path.

If DOM extraction is needed, select the last assistant message after the latest
user packet prompt. Then save the extracted text as a local `.md` file.

Do not save:

- the user prompt
- an older assistant turn
- a short interim fragment while generation is still active
- a tool status label such as `已调用工具`

The saved local Markdown review should contain the requested headings or
otherwise clearly answer the current packet.
