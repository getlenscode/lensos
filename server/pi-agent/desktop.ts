import { execFile } from "node:child_process";
import { promisify } from "node:util";
import { Type } from "@earendil-works/pi-ai";
import { defineTool } from "@earendil-works/pi-coding-agent";

const run = promisify(execFile);
let actionCount = 0;
let screenshotCount = 0;

export default function (pi) {
    pi.registerTool(defineTool({
        name: "desktop_action",
        label: "Desktop action",
        description: "Control and observe the VoiceOS desktop. Commands: workspace 1-10, next/previous workspace, open/close terminal, close window, close the window/terminal/browser/files on the left/right/top/bottom, a corner like bottom-right, or on workspace N, focus left/right/up/down, focus <app or window name> (e.g. focus the browser, focus nvim), or focus the left/right/top/bottom/bottom-right terminal/window or a window on workspace N, split vertical/horizontal/toggle or split the top/bottom/left/right/bottom-right terminal vertical/horizontal (opens a new terminal pane; vertical = side by side, horizontal = stacked), open <app or file or folder> (e.g. open browseros, open Downloads folder), open browser or URL (opens BrowserOS; firefox/chromium available by name), search web, search files, cd to <any directory> or cd to <dir> in the top/bottom/left/right terminal, run <shell command> in (the <pos>) terminal, type <text> in (the <pos>) window/terminal (supports {enter} {esc} {tab} {backspace} {ctrl+c} placeholders), press <keys like ctrl+c or enter> in (the <pos>) window/terminal, open <path> in <app> (e.g. open ~/test.txt in vim). Observation commands: list windows; read (the <pos>) terminal — returns scrollback text; read (the <pos>) browser/page — returns page text; get the browser url; read file <path>; list files (in <dir>); screenshot / look at the screen — returns an image (only when enabled). Copy to clipboard: copy the terminal output / copy the error / copy that (copies terminal scrollback), copy the browser page, copy file <path>. Appearance: change/next/random wallpaper, set wallpaper to <name or path>. tmux (a persistent session shown in a terminal window): open tmux, new tmux window [called <name>], split tmux [vertically|horizontally], run <cmd> in tmux / tmux type <text>, read tmux (pane output), list tmux. Pointer commands: click [at X Y] / right-click / double-click [on the <pos> window/terminal/browser/files], scroll up/down/left/right [N times] [in the <pos> window], drag X Y to X Y, drag the <pos> window to the left/right/top/bottom. Every action returns the current window list so you can verify results before replying. Use one command per call.",
        parameters: Type.Object({
            command: Type.String({ description: "A VoiceOS desktop command, such as 'workspace 2', 'read the top terminal', or 'click on the left terminal'." }),
        }),
        async execute(_id, params) {
            if (++actionCount > 10) throw new Error("Maximum of ten desktop actions per request.");
            const python = process.env.VOICEOS_PYTHON || "/opt/voiceos-venv/bin/python";
            const tool = process.env.VOICEOS_DESKTOP_TOOL || "/opt/noVNC/server/desktop_tool.py";
            try {
                const { stdout } = await run(python, [tool, params.command], {
                    timeout: 15000,
                    maxBuffer: 4 * 1024 * 1024, // screenshots are base64 JPEGs
                });
                const result = JSON.parse(stdout);
                if (result.image) {
                    if (++screenshotCount > 3) {
                        throw new Error("Maximum of three screenshots per request.");
                    }
                    const { image, mime, ...rest } = result;
                    return {
                        content: [
                            { type: "text", text: JSON.stringify(rest) },
                            { type: "image", data: image, mimeType: mime || "image/jpeg" },
                        ],
                        details: rest,
                    };
                }
                return { content: [{ type: "text", text: JSON.stringify(result) }], details: result };
            } catch (error) {
                let message = error.message || "Desktop action failed.";
                try { message = JSON.parse(error.stdout).error || message; } catch (_) { /* keep original */ }
                throw new Error(message);
            }
        },
    }));
}
