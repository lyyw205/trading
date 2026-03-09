#!/usr/bin/env node

// Claude Code hook script for DevBoard Agent Monitor
// Reads hook event from stdin and sends data to the ingest API

import { readFileSync } from "fs";
import { resolve, dirname } from "path";
import { fileURLToPath } from "url";

// Load .env from project root
const __dirname = dirname(fileURLToPath(import.meta.url));
const envPath = resolve(__dirname, "..", ".env");
const envVars = {};
try {
  const envContent = readFileSync(envPath, "utf8");
  for (const line of envContent.split("\n")) {
    const match = line.match(/^([^#=]+)=(.*)$/);
    if (match) envVars[match[1].trim()] = match[2].trim();
  }
} catch {}

const DEVBOARD_URL = process.env.DEVBOARD_URL || envVars.DEVBOARD_URL || "http://localhost:3000";
const SYNC_TOKEN = process.env.SYNC_API_TOKEN || envVars.SYNC_API_TOKEN || "";

let input = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (chunk) => (input += chunk));
process.stdin.on("end", async () => {
  try {
    const event = JSON.parse(input);
    const sessionId = event.session_id;
    const cwd = event.cwd;
    const hookEvent = event.hook_event_name;

    if (!sessionId || !cwd || !SYNC_TOKEN) return;

    const body = {
      sessionId,
      folderPath: cwd,
    };

    if (hookEvent === "PostToolUse") {
      const toolName = event.tool_name || "unknown";
      const toolInput = event.tool_input;

      // Extract meaningful title from tool input
      let title = toolName;
      if (toolInput) {
        try {
          const parsed = typeof toolInput === "string" ? JSON.parse(toolInput) : toolInput;
          if (parsed.file_path) title = `${toolName}: ${parsed.file_path.split("/").pop()}`;
          else if (parsed.command) title = `${toolName}: ${parsed.command.slice(0, 80)}`;
          else if (parsed.pattern) title = `${toolName}: ${parsed.pattern}`;
          else if (parsed.query) title = `${toolName}: ${parsed.query.slice(0, 80)}`;
          else if (parsed.prompt) title = `${toolName}: ${parsed.prompt.slice(0, 80)}`;
        } catch {}
      }

      body.logs = [{ logType: "tool_call", title }];
    } else if (hookEvent === "UserPromptSubmit") {
      body.heartbeat = true;
    } else if (hookEvent === "Stop") {
      body.heartbeat = true;
    }

    await fetch(`${DEVBOARD_URL}/api/monitor/ingest`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-Sync-Token": SYNC_TOKEN,
      },
      body: JSON.stringify(body),
      signal: AbortSignal.timeout(3000),
    }).catch(() => {});
  } catch {}
});
