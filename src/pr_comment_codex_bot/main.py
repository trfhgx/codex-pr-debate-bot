from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from .env_file import update_env_values
from .security import verify_github_signature
from .service import PRCommentService
from .settings import Settings
from .storage import Storage

settings = Settings()
storage = Storage(settings.database_path)
service = PRCommentService(settings=settings, storage=storage)
poll_task: asyncio.Task[None] | None = None
webhook_sync_task: asyncio.Task[None] | None = None
_last_tunnel_webhook_url: str | None = None
ENV_PATH = Path(".env")


def reload_settings() -> Settings:
    global settings
    settings = Settings()
    service.settings = settings
    return settings


def enrich_watched_repo(watch: dict[str, object]) -> dict[str, object]:
    return service.enrich_watched_repo(watch)


def set_webhook_sync_task_enabled(enabled: bool) -> None:
    global webhook_sync_task, _last_tunnel_webhook_url
    if enabled:
        if webhook_sync_task is None or webhook_sync_task.done():
            webhook_sync_task = asyncio.create_task(_tunnel_webhook_sync_loop())
    elif webhook_sync_task is not None:
        webhook_sync_task.cancel()
        webhook_sync_task = None
        _last_tunnel_webhook_url = None

app = FastAPI(title=settings.app_name)


DASHBOARD_HTML = """\n<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PR Comment Codex Bot Dashboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@400;500;600;700;800&display=swap" rel="stylesheet">
  <style>
    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }
    :root {
      --bg-color: #000000;
      --accent-green: #c6ff00;
      --card-dark: #121214;
      --card-border: #1f1f23;
      --text-muted: #4e4e52;
      --text-secondary: #8c8c90;
      --text-white: #ffffff;
      --accent-yellow: #ffe359;
      --accent-orange: #ff7c3b;
      --accent-blue: #85a9ff;
    }
    body {
      background: var(--bg-color);
      color: var(--text-white);
      font-family: 'Outfit', ui-sans-serif, system-ui, -apple-system, sans-serif;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 40px 20px;
      overflow-x: hidden;
    }
    .showcase-container {
      width: 100%;
      max-width: 960px;
      display: flex;
      flex-direction: column;
      gap: 32px;
    }
    .dashboard-grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 28px;
    }
    .card {
      border-radius: 36px;
      padding: 28px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      position: relative;
      overflow: hidden;
      background: var(--card-dark);
      border: 1px solid var(--card-border);
      transition: transform 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275);
    }
    .card:hover {
      transform: scale(1.015);
    }
    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      width: 100%;
      margin-bottom: 12px;
    }
    .card-label {
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    
    /* Card Sizes */
    .card-sm {
      height: 200px;
    }
    .card-md {
      height: 240px;
    }
    .card-lg {
      height: 508px; /* 240 + 240 + 28 */
      grid-row: span 2;
    }

    /* Common Form / Inputs inside cards */
    .card-select, .card-input {
      background: rgba(0, 0, 0, 0.25);
      border: 1px solid rgba(0, 0, 0, 0.15);
      border-radius: 999px;
      padding: 6px 14px;
      font-size: 11px;
      font-weight: 600;
      color: inherit;
      outline: none;
      transition: background 0.2s, border-color 0.2s;
    }
    .card-select:hover, .card-input:focus {
      background: rgba(0, 0, 0, 0.35);
      border-color: rgba(0, 0, 0, 0.3);
    }
    .card-select {
      cursor: pointer;
      appearance: none;
      -webkit-appearance: none;
      padding-right: 26px;
      background-image: url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' fill='none' stroke='%23000000' stroke-width='1.5'%3E%3Cpath d='M1 1l4 4 4-4'/%3E%3C/svg%3E");
      background-repeat: no-repeat;
      background-position: right 12px center;
    }
    .card-select-dark {
      color: var(--text-white);
      background-image: url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' fill='none' stroke='%23ffffff' stroke-width='1.5'%3E%3Cpath d='M1 1l4 4 4-4'/%3E%3C/svg%3E");
    }

    /* Card 1: Status (Green) */
    .status-card {
      background: var(--accent-green);
      color: #000000;
      grid-column: span 1;
      border: none;
    }
    .status-pill {
      background: #000000;
      color: var(--text-white);
      padding: 4px 10px;
      border-radius: 999px;
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.05em;
    }
    .status-value {
      font-size: 38px;
      font-weight: 800;
      letter-spacing: -0.04em;
      line-height: 1.1;
      margin-top: 14px;
    }
    .status-sub {
      font-size: 11px;
      font-weight: 600;
      opacity: 0.7;
    }

    /* Card 2: Watched Repos (Dark) */
    .repos-card {
      grid-column: span 1;
    }
    .repos-header-btn {
      width: 20px;
      height: 20px;
      border-radius: 50%;
      background: rgba(255, 255, 255, 0.08);
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      border: none;
      color: var(--text-white);
      font-weight: bold;
      font-size: 12px;
    }
    .repos-list {
      display: flex;
      flex-direction: column;
      gap: 8px;
      overflow-y: auto;
      flex: 1;
      margin-top: 10px;
      padding-right: 2px;
    }
    .repos-list::-webkit-scrollbar {
      width: 4px;
    }
    .repos-list::-webkit-scrollbar-thumb {
      background: rgba(255, 255, 255, 0.1);
      border-radius: 999px;
    }
    .repos-toolbar {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-top: 10px;
      padding: 8px 10px;
      border-radius: 12px;
      background: rgba(255, 255, 255, 0.03);
      border: 1px solid rgba(255, 255, 255, 0.05);
    }
    .repos-toolbar-label {
      display: flex;
      flex-direction: column;
      gap: 2px;
      min-width: 0;
    }
    .repos-toolbar-title {
      font-size: 10px;
      font-weight: 800;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--text-secondary);
    }
    .repos-toolbar-sub {
      font-size: 9px;
      font-weight: 600;
      color: var(--text-muted);
    }
    .repo-item {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto auto;
      gap: 8px;
      align-items: center;
      font-size: 11px;
      background: rgba(255, 255, 255, 0.03);
      padding: 6px 10px;
      border-radius: 12px;
      border: 1px solid rgba(255, 255, 255, 0.05);
    }
    .repo-item.disconnected {
      border-color: rgba(248, 113, 113, 0.35);
      background: rgba(248, 113, 113, 0.06);
    }
    .repo-meta {
      display: flex;
      flex-direction: column;
      gap: 2px;
      min-width: 0;
    }
    .repo-name {
      color: var(--text-white);
      text-decoration: none;
      font-weight: 600;
      white-space: nowrap;
      overflow: hidden;
      text-overflow: ellipsis;
    }
    .repo-status {
      color: var(--text-muted);
      font-size: 9px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.04em;
    }
    .repo-status.live { color: #34d399; }
    .repo-status.off { color: #71717a; }
    .repo-status.warn { color: #fbbf24; }
    .repo-status.err { color: #f87171; }
    .repo-status-dot {
      display: inline-block;
      width: 6px;
      height: 6px;
      border-radius: 50%;
      margin-right: 4px;
      vertical-align: middle;
      background: currentColor;
    }
    .repo-toggle {
      position: relative;
      display: inline-flex;
      width: 34px;
      height: 18px;
      flex-shrink: 0;
    }
    .repo-toggle input {
      opacity: 0;
      width: 0;
      height: 0;
      position: absolute;
    }
    .repo-toggle-slider {
      position: absolute;
      inset: 0;
      background: rgba(255, 255, 255, 0.12);
      border-radius: 999px;
      transition: background 0.2s;
      cursor: pointer;
    }
    .repo-toggle-slider::before {
      content: "";
      position: absolute;
      width: 14px;
      height: 14px;
      left: 2px;
      top: 2px;
      background: #ffffff;
      border-radius: 50%;
      transition: transform 0.2s;
    }
    .repo-toggle input:checked + .repo-toggle-slider {
      background: var(--accent-green);
    }
    .repo-toggle input:checked + .repo-toggle-slider::before {
      transform: translateX(16px);
      background: #000000;
    }
    .repo-name:hover {
      text-decoration: underline;
      color: var(--accent-green);
    }
    .repo-delete {
      background: none;
      border: none;
      color: var(--text-muted);
      cursor: pointer;
      font-size: 14px;
      font-weight: bold;
      transition: color 0.2s;
    }
    .repo-delete:hover {
      color: #ef4444;
    }
    .repo-add-form {
      display: flex;
      gap: 6px;
      margin-top: 8px;
    }
    .repo-add-input {
      flex: 1;
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 999px;
      padding: 4px 10px;
      font-size: 10px;
      color: var(--text-white);
      outline: none;
    }
    .repo-add-submit {
      background: var(--text-white);
      color: #000000;
      border: none;
      border-radius: 999px;
      padding: 4px 8px;
      font-size: 10px;
      font-weight: 700;
      cursor: pointer;
    }

    /* Card 3: Webhook Tunnel (White) */
    .tunnel-card {
      background: #ffffff;
      color: #000000;
      grid-column: span 2;
      border: none;
    }
    .tunnel-url-container {
      background: rgba(0, 0, 0, 0.04);
      border: 1px dashed rgba(0, 0, 0, 0.15);
      border-radius: 18px;
      padding: 10px 14px;
      font-family: monospace;
      font-size: 11px;
      display: flex;
      justify-content: space-between;
      align-items: center;
      cursor: pointer;
      margin-top: 14px;
      transition: background 0.2s;
    }
    .tunnel-url-container:hover {
      background: rgba(0, 0, 0, 0.08);
    }
    .tunnel-url {
      font-weight: 600;
      word-break: break-all;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      max-width: 90%;
    }
    .copy-icon {
      opacity: 0.6;
    }
    .perf-bars {
      display: flex;
      align-items: flex-end;
      gap: 5px;
      height: 48px;
      margin-top: 8px;
    }
    .perf-bar {
      background: var(--accent-blue);
      border-radius: 999px;
      transition: height 1s ease;
    }
    .perf-bar-slit {
      background: var(--accent-blue);
      border-radius: 999px;
      display: flex;
      align-items: center;
      justify-content: flex-end;
      padding-right: 6px;
    }
    .slit-inner {
      width: 4px;
      height: 60%;
      background: #ffffff;
      border-radius: 999px;
      opacity: 0.9;
    }

    /* Card 4: Webhook Events Log (Dark) */
    .events-card {
      grid-column: span 2;
    }
    .events-list {
      display: flex;
      flex-direction: column;
      gap: 6px;
      margin-top: 12px;
      overflow-y: auto;
      flex: 1;
      padding-right: 2px;
    }
    .events-list::-webkit-scrollbar {
      width: 4px;
    }
    .events-list::-webkit-scrollbar-thumb {
      background: rgba(255, 255, 255, 0.1);
      border-radius: 999px;
    }
    .event-item {
      display: flex;
      justify-content: space-between;
      align-items: center;
      background: rgba(255, 255, 255, 0.02);
      border: 1px solid rgba(255, 255, 255, 0.04);
      padding: 10px 14px;
      border-radius: 18px;
      font-size: 11px;
      cursor: pointer;
      transition: background 0.2s, border-color 0.2s;
    }
    .event-item:hover {
      background: rgba(255, 255, 255, 0.05);
      border-color: rgba(255, 255, 255, 0.08);
    }
    .event-left {
      display: flex;
      flex-direction: column;
      gap: 2px;
    }
    .event-id {
      font-family: monospace;
      font-weight: 700;
      color: var(--text-secondary);
    }
    .event-summary {
      color: var(--text-white);
      max-width: 200px;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .event-status {
      padding: 3px 8px;
      border-radius: 999px;
      font-size: 9px;
      font-weight: 700;
      text-transform: uppercase;
    }
    .status-success {
      background: rgba(198, 255, 0, 0.15);
      color: var(--accent-green);
    }
    .status-failed {
      background: rgba(239, 68, 68, 0.15);
      color: #f87171;
    }
    .status-pending {
      background: rgba(255, 255, 255, 0.08);
      color: var(--text-secondary);
    }

    /* Card 5: Balance Gauge (Yellow) */
    .balance-card {
      background: var(--accent-yellow);
      color: #000000;
      grid-column: span 2;
      border: none;
    }
    .balance-main {
      display: flex;
      justify-content: space-between;
      align-items: center;
      flex: 1;
      margin-top: 12px;
    }
    .balance-info {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .balance-legend {
      font-size: 12px;
      font-weight: 700;
      display: flex;
      align-items: center;
      gap: 6px;
    }
    .legend-dot {
      width: 6px;
      height: 6px;
      border-radius: 50%;
      background: #000000;
    }
    .legend-dot-faded {
      background: rgba(0, 0, 0, 0.25);
    }
    .legend-text-faded {
      opacity: 0.4;
    }
    .gauge-wrapper {
      position: relative;
      width: 140px;
      height: 70px;
      overflow: hidden;
    }
    .gauge-ticks {
      position: absolute;
      width: 100%;
      height: 100%;
      top: 0;
      left: 0;
    }
    .tick {
      position: absolute;
      left: 50%;
      bottom: 0;
      width: 2px;
      height: 6px;
      background: rgba(0, 0, 0, 0.15);
      transform-origin: 50% 100%;
    }
    .tick.active {
      background: #000000;
      width: 2.5px;
      height: 9px;
    }
    .gauge-center {
      position: absolute;
      bottom: 0;
      left: 0;
      width: 100%;
      text-align: center;
      display: flex;
      flex-direction: column;
      align-items: center;
      line-height: 1;
    }
    .gauge-val {
      font-size: 28px;
      font-weight: 800;
    }
    .gauge-label {
      font-size: 8px;
      font-weight: 700;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      opacity: 0.5;
      margin-top: 3px;
    }

    /* Card 6: Bot Config / Settings (Orange, Tall) */
    .config-card {
      background: var(--accent-orange);
      color: #000000;
      grid-column: span 2;
      border: none;
    }
    .config-form {
      display: flex;
      flex-direction: column;
      gap: 14px;
      margin-top: 14px;
      flex: 1;
      justify-content: center;
    }
    .config-group {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }
    .config-group label {
      font-size: 10px;
      font-weight: 800;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      opacity: 0.6;
    }
    .config-input-row {
      display: flex;
      gap: 8px;
    }
    .config-input {
      flex: 1;
      background: rgba(0, 0, 0, 0.08);
      border: 1px solid rgba(0, 0, 0, 0.1);
      border-radius: 12px;
      padding: 8px 12px;
      font-size: 12px;
      font-weight: 600;
      color: #000000;
      outline: none;
    }
    .config-input::placeholder {
      color: rgba(0, 0, 0, 0.35);
    }
    .config-pills {
      display: flex;
      background: rgba(0, 0, 0, 0.08);
      border-radius: 999px;
      padding: 2px;
    }
    .config-pill {
      flex: 1;
      border: none;
      background: none;
      padding: 6px 12px;
      font-size: 10px;
      font-weight: 700;
      border-radius: 999px;
      cursor: pointer;
      color: rgba(0, 0, 0, 0.5);
      transition: background 0.2s, color 0.2s;
    }
    .config-pill.active {
      background: #000000;
      color: var(--text-white);
    }
    .config-status-label {
      font-size: 10px;
      font-weight: 700;
      opacity: 0.8;
      margin-top: 2px;
    }
    .config-submit-btn {
      background: #000000;
      color: var(--text-white);
      border: none;
      border-radius: 999px;
      padding: 10px 16px;
      font-size: 12px;
      font-weight: 700;
      cursor: pointer;
      margin-top: 10px;
      transition: opacity 0.2s;
    }
    .config-submit-btn:hover {
      opacity: 0.9;
    }

    /* Detail Dialog Modal */
    dialog {
      border: 1px solid var(--card-border);
      background: var(--card-dark);
      color: var(--text-white);
      padding: 28px;
      border-radius: 28px;
      max-width: 600px;
      width: 90%;
      outline: none;
      margin: auto;
    }
    dialog::backdrop {
      background: rgba(0, 0, 0, 0.7);
      backdrop-filter: blur(4px);
    }
    .detail-head h3 {
      font-size: 18px;
      font-weight: 800;
      letter-spacing: -0.01em;
    }
    pre {
      background: rgba(0, 0, 0, 0.3);
      padding: 16px;
      border-radius: 16px;
      font-family: monospace;
      font-size: 11px;
      overflow-x: auto;
      margin-top: 14px;
      color: var(--accent-blue);
      border: 1px solid var(--card-border);
    }
    .btn-close {
      background: var(--text-white);
      color: #000000;
      border: none;
      border-radius: 999px;
      padding: 6px 14px;
      font-size: 11px;
      font-weight: 700;
      cursor: pointer;
    }

    /* Footer Showcase Branding */
    .dashboard-footer {
      display: flex;
      justify-content: space-between;
      align-items: center;
      color: var(--text-muted);
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 0.05em;
      padding: 0 10px;
      margin-top: -8px;
    }
    .dashboard-footer span {
      text-transform: uppercase;
    }

    /* Responsive scaling */
    @media (max-width: 900px) {
      .dashboard-grid {
        grid-template-columns: repeat(2, 1fr);
      }
      .card-lg {
        grid-row: span 1;
        height: auto;
      }
    }
    @media (max-width: 580px) {
      body {
        padding: 20px 14px;
      }
      .dashboard-grid {
        grid-template-columns: 1fr;
        gap: 20px;
      }
      .card {
        grid-column: span 1 !important;
        height: auto !important;
        min-height: 180px;
        border-radius: 28px;
      }
      .card-lg {
        min-height: 300px;
      }
    }
  </style>
</head>
<body>
  <div class="showcase-container">
    <div class="dashboard-grid">
      <!-- Card 1: System Status (Green) -->
      <div class="card card-sm status-card">
        <div class="card-header">
          <span class="card-label" style="color: #000000;">SYSTEM STATUS</span>
          <span class="status-pill" id="status-pill">OFFLINE</span>
        </div>
        <div class="status-value" id="status-val">↓ OFFLINE</div>
        <div class="status-sub" id="status-sub">No forwarding active</div>
      </div>

      <!-- Card 2: Watched Repos (Dark) -->
      <div class="card card-sm repos-card">
        <div class="card-header">
          <span class="card-label" style="color: var(--text-secondary);">WATCHING</span>
          <button class="repos-header-btn" id="repo-toggle-btn">+</button>
        </div>
        <div class="repos-toolbar">
          <div class="repos-toolbar-label">
            <span class="repos-toolbar-title">Always on</span>
            <span class="repos-toolbar-sub" id="always-on-sub">Auto-sync webhooks when tunnel changes</span>
          </div>
          <label class="repo-toggle" title="Automatically sync webhooks for enabled repos">
            <input type="checkbox" id="always-on-toggle" checked>
            <span class="repo-toggle-slider"></span>
          </label>
        </div>
        <div class="repos-list" id="repos-list">
          <div class="repo-item" style="justify-content: center; color: var(--text-muted);">No repos watched</div>
        </div>
        <form class="repo-add-form" id="repo-form" style="display: none;">
          <input class="repo-add-input" type="url" id="repo-input" placeholder="github.com/owner/repo" required>
          <button class="repo-add-submit" type="submit">Add</button>
        </form>
      </div>

      <!-- Card 3: Webhook Tunnel (White) -->
      <div class="card card-sm tunnel-card">
        <div class="card-header">
          <span class="card-label" style="color: #000000;">WEBHOOK TUNNEL</span>
          <div class="perf-bars">
            <div class="perf-bar" style="width: 5px; height: 35%;"></div>
            <div class="perf-bar" style="width: 5px; height: 50%;"></div>
            <div class="perf-bar" style="width: 5px; height: 40%;"></div>
            <div class="perf-bar" style="width: 5px; height: 65%;"></div>
            <div class="perf-bar" style="width: 5px; height: 55%;"></div>
            <div class="perf-bar-slit" style="width: 24px; height: 80%;">
              <div class="slit-inner"></div>
            </div>
            <div class="perf-bar" style="width: 6px; height: 90%;"></div>
          </div>
        </div>
        <div class="tunnel-url-container" id="tunnel-container" title="Click to copy Webhook URL">
          <span class="tunnel-url" id="tunnel-url">Checking tunnel...</span>
          <svg class="copy-icon" width="12" height="12" fill="none" stroke="currentColor" stroke-width="2.5" viewBox="0 0 24 24"><path d="M8 7v12a2 2 0 002 2h8a2 2 0 002-2V7a2 2 0 00-2-2h-8a2 2 0 00-2 2zM8 7H6a2 2 0 00-2 2v10a2 2 0 002 2h2M16 5V3a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2h2" stroke-linecap="round" stroke-linejoin="round"></svg>
        </div>
      </div>

      <!-- Card 4: Webhook Events Log (Dark) -->
      <div class="card card-md events-card">
        <div class="card-header">
          <span class="card-label" style="color: var(--text-secondary);">RECENT ACTIVITY</span>
          <button type="button" class="card-select card-select-dark" id="refresh-btn" style="border: 1px solid rgba(255, 255, 255, 0.1); padding: 4px 12px; border-radius: 999px;">Refresh</button>
        </div>
        <div class="events-list" id="events-list">
          <div class="event-item" style="justify-content: center; color: var(--text-muted); cursor: default;">No events received</div>
        </div>
      </div>

      <!-- Card 6: Bot Config / Settings (Orange, Tall) -->
      <div class="card card-lg config-card">
        <div class="card-header">
          <span class="card-label" style="color: #000000;">BOT CONFIG</span>
        </div>
        <form class="config-form" id="config-form">
          <div class="config-group">
            <label>Holder Account</label>
            <div class="config-input-row">
              <input class="config-input" type="text" id="holder-login" placeholder="holder_username" required>
            </div>
            <div class="config-pills" data-account="holder">
              <button type="button" class="config-pill active" data-auth="gh_cli">gh CLI</button>
              <button type="button" class="config-pill" data-auth="token">Token</button>
            </div>
            <div class="config-input-row" id="holder-token-row" style="display: none; margin-top: 6px;">
              <input class="config-input" type="password" id="holder-token" placeholder="GitHub Personal Access Token">
            </div>
            <div class="config-status-label" id="holder-status" style="color: rgba(0, 0, 0, 0.65);">Checking status...</div>
          </div>

          <div class="config-group">
            <label>Replier Account</label>
            <div class="config-input-row">
              <input class="config-input" type="text" id="replier-login" placeholder="replier_username" required>
            </div>
            <div class="config-pills" data-account="replier">
              <button type="button" class="config-pill active" data-auth="gh_cli">gh CLI</button>
              <button type="button" class="config-pill" data-auth="token">Token</button>
            </div>
            <div class="config-input-row" id="replier-token-row" style="display: none; margin-top: 6px;">
              <input class="config-input" type="password" id="replier-token" placeholder="GitHub Personal Access Token">
            </div>
            <div class="config-status-label" id="replier-status" style="color: rgba(0, 0, 0, 0.65);">Checking status...</div>
          </div>

          <div class="config-group">
            <label>Permissions Mode</label>
            <select class="card-select" id="holder-permission" style="width: 100%; background-color: rgba(0,0,0,0.08); font-weight: 700;">
              <option value="admin">Admin</option>
              <option value="write">Write</option>
              <option value="triage">Triage</option>
            </select>
          </div>

          <button class="config-submit-btn" type="submit">Save Settings</button>
        </form>
      </div>

      <!-- Card 5: Balance Gauge (Yellow) -->
      <div class="card card-md balance-card">
        <div class="card-header">
          <span class="card-label" style="color: #000000;">DELIVERY RATE</span>
          <select class="card-select" id="delivery-select">
            <option>This Month</option>
          </select>
        </div>
        <div class="balance-main">
          <div class="balance-info">
            <div class="balance-legend">
              <span class="legend-dot"></span>
              <span id="legend-success">Success - 100%</span>
            </div>
            <div class="balance-legend">
              <span class="legend-dot legend-dot-faded"></span>
              <span class="legend-text-faded" id="legend-failed">Failed - 0%</span>
            </div>
          </div>
          <div class="gauge-wrapper">
            <div class="gauge-ticks" id="gauge-ticks">
              <!-- Radial Ticks generated in JS -->
            </div>
            <div class="gauge-center">
              <div class="gauge-val" id="gauge-val">100%</div>
              <div class="gauge-label">Deliveries</div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- Bottom showcase footer -->
    <div class="dashboard-footer">
      <span>@tou.visuals</span>
      <span>Swipe &gt;</span>
    </div>
  </div>

  <dialog id="detail-modal">
    <div class="detail-head" style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--card-border); padding-bottom: 14px;">
      <h3 id="detail-title">Event Detail</h3>
      <button type="button" class="btn-close" id="close-modal-btn">Close</button>
    </div>
    <pre id="detail-json"></pre>
  </dialog>

  <script>
    const eventsList = document.querySelector("#events-list");
    const reposList = document.querySelector("#repos-list");
    const detailModal = document.querySelector("#detail-modal");
    const detailJson = document.querySelector("#detail-json");
    const detailTitle = document.querySelector("#detail-title");
    const tunnelUrlEl = document.querySelector("#tunnel-url");
    const tunnelContainer = document.querySelector("#tunnel-container");
    const statusPill = document.querySelector("#status-pill");
    const statusVal = document.querySelector("#status-val");
    const statusSub = document.querySelector("#status-sub");
    
    const repoToggleBtn = document.querySelector("#repo-toggle-btn");
    const alwaysOnToggle = document.querySelector("#always-on-toggle");
    const alwaysOnSub = document.querySelector("#always-on-sub");
    let currentTunnelWebhookUrl = null;
    const repoForm = document.querySelector("#repo-form");
    const repoInput = document.querySelector("#repo-input");

    let allEvents = [];
    let allWatches = [];
    let holderAuth = "gh_cli";
    let replierAuth = "gh_cli";

    function esc(value) {
      return String(value ?? "").replace(/[&<>"']/g, ch => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[ch]));
    }

    async function copyToClipboard(text, el) {
      try {
        await navigator.clipboard.writeText(text);
        const originalContent = el.innerHTML;
        el.innerHTML = '<span style="font-weight: 700; color: #16a34a; font-size: 11px;">Copied webhook URL!</span>';
        setTimeout(() => {
          el.innerHTML = originalContent;
        }, 2000);
      } catch (err) {
        console.error('Failed to copy: ', err);
      }
    }

    repoToggleBtn.addEventListener("click", () => {
      if (repoForm.style.display === "none") {
        repoForm.style.display = "flex";
        repoToggleBtn.textContent = "-";
        repoInput.focus();
      } else {
        repoForm.style.display = "none";
        repoToggleBtn.textContent = "+";
      }
    });

    document.querySelector("#repo-form").addEventListener("submit", async e => {
      e.preventDefault();
      const response = await fetch("/watched-repos", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: repoInput.value })
      });
      if (!response.ok) {
        alert(await response.text());
        return;
      }
      repoInput.value = "";
      repoForm.style.display = "none";
      repoToggleBtn.textContent = "+";
      await refreshAll();
    });

    async function refreshAll() {
      await Promise.all([
        loadEvents(),
        loadTunnelInfo(),
        loadSyncSettings(),
        loadWatches(),
        loadAccountSettings()
      ]);
    }

    async function loadEvents() {
      try {
        const response = await fetch("/events?limit=20", { cache: "no-store" });
        allEvents = await response.json();
        
        if (!allEvents.length) {
          eventsList.innerHTML = '<div class="event-item" style="justify-content: center; color: var(--text-muted); cursor: default;">No events received yet.</div>';
          updateDeliveryGauge(100, 0);
          return;
        }

        eventsList.innerHTML = allEvents.slice(0, 5).map(event => {
          const isSuccess = ['received', 'polled', 'watched', 'created', 'updated', 'ready_to_implement', 'implementing', 'implemented'].includes(event.status);
          const statusClass = isSuccess ? 'status-success' : 'status-failed';
          return `
            <div class="event-item" data-id="${esc(event.id)}">
              <div class="event-left">
                <span class="event-id">${esc(event.id.slice(0, 8))}</span>
                <span class="event-summary">${esc(event.summary)}</span>
              </div>
              <span class="event-status ${statusClass}">${esc(event.status)}</span>
            </div>
          `;
        }).join("");

        document.querySelectorAll(".event-item[data-id]").forEach(item => {
          item.addEventListener("click", async () => {
            const id = item.getAttribute("data-id");
            const detailResponse = await fetch(`/events/${id}`, { cache: "no-store" });
            const event = await detailResponse.json();
            detailTitle.textContent = `Event ${id.slice(0, 8)}`;
            detailJson.textContent = JSON.stringify(event, null, 2);
            detailModal.showModal();
          });
        });

        // Calculate delivery rate
        const total = allEvents.length;
        const successes = allEvents.filter(e => ['received', 'polled', 'watched', 'created', 'updated', 'ready_to_implement', 'implementing', 'implemented'].includes(e.status)).length;
        const successRate = total > 0 ? Math.round((successes / total) * 100) : 100;
        updateDeliveryGauge(successRate, 100 - successRate);
      } catch (err) {
        console.error("Error loading events: ", err);
      }
    }

    async function loadWatches() {
      try {
        const response = await fetch("/watched-repos", { cache: "no-store" });
        allWatches = await response.json();

        if (!allWatches.length) {
          reposList.innerHTML = '<div class="repo-item" style="justify-content: center; color: var(--text-muted);">No repos watched</div>';
          return;
        }

        reposList.innerHTML = allWatches.map(watch => {
          const enabled = !!watch.enabled;
          const connected = !!watch.connected;
          const status = watch.connection_status || "not_connected";
          const label = watch.connection_label || (enabled ? "Not connected" : "Off");
          let statusClass = "off";
          if (!enabled) statusClass = "off";
          else if (connected) statusClass = "live";
          else if (status === "connecting") statusClass = "warn";
          else statusClass = "err";
          const itemClass = enabled && !connected ? "repo-item disconnected" : "repo-item";
          return `
            <div class="${itemClass}">
              <div class="repo-meta">
                <a href="${esc(watch.url)}" target="_blank" class="repo-name">${esc(watch.repo_full_name)}</a>
                <span class="repo-status ${statusClass}">
                  <span class="repo-status-dot"></span>${esc(label)}
                </span>
              </div>
              <label class="repo-toggle" title="${enabled ? "Turn off this repo" : "Turn on this repo"}">
                <input type="checkbox" data-toggle="${esc(watch.id)}" ${enabled ? "checked" : ""}>
                <span class="repo-toggle-slider"></span>
              </label>
              <button class="repo-delete" data-delete="${esc(watch.id)}" title="Remove repo">×</button>
            </div>
          `;
        }).join("");

        document.querySelectorAll("input[data-toggle]").forEach(input => {
          input.addEventListener("change", async () => {
            const watchId = input.getAttribute("data-toggle");
            const enabled = input.checked;
            input.disabled = true;
            try {
              const response = await fetch(`/watched-repos/${watchId}`, {
                method: "PATCH",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ enabled })
              });
              if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || "Failed to update repo");
              }
              await refreshAll();
            } catch (err) {
              input.checked = !enabled;
              alert(err.message || "Failed to update repo");
            } finally {
              input.disabled = false;
            }
          });
        });

        document.querySelectorAll("button[data-delete]").forEach(button => {
          button.addEventListener("click", async (e) => {
            e.stopPropagation();
            if (confirm("Stop watching this repository?")) {
              await fetch(`/watched-repos/${button.getAttribute("data-delete")}`, { method: "DELETE" });
              await refreshAll();
            }
          });
        });
      } catch (err) {
        console.error("Error loading watches: ", err);
      }
    }

    async function loadSyncSettings() {
      try {
        const response = await fetch("/settings/sync", { cache: "no-store" });
        const info = await response.json();
        alwaysOnToggle.checked = !!info.auto_sync_webhooks;
        alwaysOnSub.textContent = info.auto_sync_webhooks
          ? "Auto-sync enabled — webhooks update when tunnel changes"
          : "Auto-sync off — use per-repo switch and manual sync";
      } catch (err) {
        console.error("Error loading sync settings: ", err);
      }
    }

    alwaysOnToggle.addEventListener("change", async () => {
      const enabled = alwaysOnToggle.checked;
      alwaysOnToggle.disabled = true;
      try {
        const response = await fetch("/settings/sync", {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ auto_sync_webhooks: enabled })
        });
        if (!response.ok) {
          const error = await response.json();
          throw new Error(error.detail || "Failed to update auto-sync");
        }
        await loadSyncSettings();
        await loadWatches();
      } catch (err) {
        alwaysOnToggle.checked = !enabled;
        alert(err.message || "Failed to update auto-sync");
      } finally {
        alwaysOnToggle.disabled = false;
      }
    });

    async function loadTunnelInfo() {
      try {
        const response = await fetch("/tunnel-info", { cache: "no-store" });
        const info = await response.json();
        currentTunnelWebhookUrl = info.github_webhook_url || null;
        
        if (info.public_url) {
          statusPill.textContent = "ONLINE";
          statusPill.style.background = "#000000";
          statusPill.style.color = "var(--accent-green)";
          statusVal.textContent = "↑ ACTIVE";
          statusSub.textContent = "Tunnel connection healthy";
          
          tunnelUrlEl.textContent = info.github_webhook_url;
          tunnelContainer.onclick = () => copyToClipboard(info.github_webhook_url, tunnelContainer);
        } else {
          statusPill.textContent = "OFFLINE";
          statusPill.style.background = "#000000";
          statusPill.style.color = "#f87171";
          statusVal.textContent = "↓ OFFLINE";
          statusSub.textContent = "No active tunnel forwarding";
          
          const localUrl = "http://127.0.0.1:8088/webhooks/github";
          tunnelUrlEl.textContent = localUrl;
          tunnelContainer.onclick = () => copyToClipboard(localUrl, tunnelContainer);
        }
      } catch (err) {
        console.error("Error loading tunnel: ", err);
      }
    }

    function setAuthPill(account, auth) {
      const pills = document.querySelector(`.config-pills[data-account="${account}"]`);
      if (!pills) return;
      pills.querySelectorAll(".config-pill").forEach(button => {
        button.classList.toggle("active", button.getAttribute("data-auth") === auth);
      });
      const tokenRow = document.querySelector(`#${account}-token-row`);
      if (tokenRow) {
        tokenRow.style.display = auth === "token" ? "flex" : "none";
      }
      if (account === "holder") holderAuth = auth;
      if (account === "replier") replierAuth = auth;
    }

    function renderAccountStatus(id, account) {
      const el = document.querySelector(id);
      if (!el) return;
      const ready = account.auth !== "missing";
      el.style.color = ready ? "#000000" : "rgba(0,0,0,0.5)";
      const login = account.login ? `@${account.login}` : "login not set";
      const authLabel = account.auth === "gh_cli"
        ? "via gh CLI"
        : account.auth === "token"
          ? (account.token_configured ? "Token Active" : "Token Missing")
          : "Auth Missing";
      el.textContent = `${login} • ${authLabel}`;
    }

    async function loadAccountSettings() {
      try {
        const response = await fetch("/settings/accounts", { cache: "no-store" });
        const data = await response.json();
        document.querySelector("#holder-login").value = data.holder.login || "";
        document.querySelector("#replier-login").value = data.replier.login || "";
        document.querySelector("#holder-permission").value = data.holder.collaborator_permission || "admin";
        
        setAuthPill("holder", data.holder.auth === "missing" ? "gh_cli" : data.holder.auth);
        setAuthPill("replier", data.replier.auth === "missing" ? "gh_cli" : data.replier.auth);
        
        renderAccountStatus("#holder-status", data.holder);
        renderAccountStatus("#replier-status", data.replier);
      } catch (err) {
        console.error("Error loading account settings: ", err);
      }
    }

    document.querySelectorAll(".config-pills").forEach(group => {
      const account = group.getAttribute("data-account");
      group.querySelectorAll(".config-pill").forEach(button => {
        button.addEventListener("click", () => {
          setAuthPill(account, button.getAttribute("data-auth"));
        });
      });
    });

    document.querySelector("#config-form").addEventListener("submit", async e => {
      e.preventDefault();
      const payload = {
        holder: {
          login: document.querySelector("#holder-login").value,
          auth: holderAuth,
          token: document.querySelector("#holder-token").value
        },
        replier: {
          login: document.querySelector("#replier-login").value,
          auth: replierAuth,
          token: document.querySelector("#replier-token").value
        },
        collaborator_permission: document.querySelector("#holder-permission").value
      };

      const response = await fetch("/settings/accounts", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload)
      });
      if (!response.ok) {
        alert(await response.text());
        return;
      }
      
      document.querySelector("#holder-token").value = "";
      document.querySelector("#replier-token").value = "";
      alert("Settings saved successfully!");
      await refreshAll();
    });

    function updateDeliveryGauge(successRate, failedRate) {
      document.querySelector("#gauge-val").textContent = `${successRate}%`;
      document.querySelector("#legend-success").textContent = `Success - ${successRate}%`;
      document.querySelector("#legend-failed").textContent = `Failed - ${failedRate}%`;
      
      const gaugeTicks = document.querySelector('#gauge-ticks');
      if (gaugeTicks) {
        gaugeTicks.innerHTML = '';
        const totalTicks = 32;
        const angleStart = -90;
        const angleEnd = 90;
        const angleStep = (angleEnd - angleStart) / (totalTicks - 1);

        for (let i = 0; i < totalTicks; i++) {
          const angle = angleStart + i * angleStep;
          const tick = document.createElement('div');
          tick.className = 'tick';
          tick.style.transform = `rotate(${angle}deg) translateY(-54px)`;
          const percentThreshold = (i / (totalTicks - 1)) * 100;
          
          if (percentThreshold <= successRate) {
            tick.classList.add('active');
          }
          gaugeTicks.appendChild(tick);
        }
      }
    }

    document.querySelector("#refresh-btn").addEventListener("click", refreshAll);
    document.querySelector("#close-modal-btn").addEventListener("click", () => detailModal.close());
    
    // Light-dismiss dialog on backdrop click
    detailModal.addEventListener('click', (event) => {
      if (event.target === detailModal) {
        detailModal.close();
      }
    });

    refreshAll();
    setInterval(loadEvents, 4000);
    setInterval(loadTunnelInfo, 5000);
    setInterval(loadWatches, 5000);
  </script>
</body>
</html>\n"""


@app.on_event("startup")
async def startup() -> None:
    await storage.init()
    global poll_task, webhook_sync_task
    if settings.github_poll_interval_seconds > 0:
        poll_task = asyncio.create_task(_poll_watched_prs_loop())
    set_webhook_sync_task_enabled(settings.github_auto_sync_webhooks)


@app.on_event("shutdown")
async def shutdown() -> None:
    if poll_task:
        poll_task.cancel()
    if webhook_sync_task:
        webhook_sync_task.cancel()


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/settings/sync")
async def get_sync_settings() -> dict[str, object]:
    return {
        "auto_sync_webhooks": settings.github_auto_sync_webhooks,
        "current_webhook_url": service._current_github_webhook_url(),
    }


@app.put("/settings/sync")
async def update_sync_settings(payload: dict[str, Any]) -> dict[str, object]:
    if "auto_sync_webhooks" not in payload:
        raise HTTPException(
            status_code=400, detail="auto_sync_webhooks field is required"
        )
    enabled = bool(payload["auto_sync_webhooks"])
    update_env_values(
        ENV_PATH, {"GITHUB_AUTO_SYNC_WEBHOOKS": "true" if enabled else "false"}
    )
    reload_settings()
    set_webhook_sync_task_enabled(enabled)
    if enabled:
        asyncio.create_task(service.sync_enabled_repo_webhooks(reason="always_on_enabled"))
    return {
        "auto_sync_webhooks": settings.github_auto_sync_webhooks,
        "current_webhook_url": service._current_github_webhook_url(),
    }


@app.get("/settings/accounts")
async def get_account_settings() -> dict[str, object]:
    return settings.accounts_status()


@app.put("/settings/accounts")
async def update_account_settings(payload: dict[str, Any]) -> dict[str, object]:
    holder = payload.get("holder") or {}
    replier = payload.get("replier") or {}
    updates: dict[str, str | None] = {}

    holder_login = str(holder.get("login") or "").strip()
    replier_login = str(replier.get("login") or "").strip()
    updates["GITHUB_HOLDER_LOGIN"] = holder_login or None
    updates["GITHUB_REPLIER_LOGIN"] = replier_login or None

    holder_auth = str(holder.get("auth") or "gh_cli")
    replier_auth = str(replier.get("auth") or "gh_cli")
    updates["GITHUB_HOLDER_USE_GH_CLI_TOKEN"] = "true" if holder_auth == "gh_cli" else "false"
    updates["GITHUB_REPLIER_USE_GH_CLI_TOKEN"] = (
        "true" if replier_auth == "gh_cli" else "false"
    )

    holder_token = str(holder.get("token") or "").strip()
    replier_token = str(replier.get("token") or "").strip()
    if holder_auth == "token":
        if holder_token:
            updates["GITHUB_HOLDER_TOKEN"] = holder_token
        elif not settings.github_holder_token:
            raise HTTPException(
                status_code=400,
                detail="Holder token is required when holder auth is set to token",
            )
    else:
        updates["GITHUB_HOLDER_TOKEN"] = None

    if replier_auth == "token":
        if replier_token:
            updates["GITHUB_REPLIER_TOKEN"] = replier_token
        elif not settings.github_replier_token:
            raise HTTPException(
                status_code=400,
                detail="Replier token is required when replier auth is set to token",
            )
    else:
        updates["GITHUB_REPLIER_TOKEN"] = None

    permission = str(holder.get("collaborator_permission") or "admin").strip() or "admin"
    updates["GITHUB_HOLDER_COLLABORATOR_PERMISSION"] = permission

    update_env_values(ENV_PATH, updates)
    reload_settings()
    return settings.accounts_status()


@app.get("/tunnel-info")
async def tunnel_info() -> dict[str, object]:
    if not settings.tunnel_info_path.exists():
        return {
            "public_url": None,
            "github_webhook_url": None,
            "provider": None,
            "status": "not_started",
            "webhook_secret_configured": bool(settings.github_webhook_secret),
            "accounts": settings.accounts_status(),
            "auto_sync_webhooks": settings.github_auto_sync_webhooks,
        }
    try:
        raw = json.loads(settings.tunnel_info_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {
            "public_url": None,
            "github_webhook_url": None,
            "provider": None,
            "status": "invalid",
            "webhook_secret_configured": bool(settings.github_webhook_secret),
            "accounts": settings.accounts_status(),
            "auto_sync_webhooks": settings.github_auto_sync_webhooks,
        }
    public_url = str(raw.get("public_url") or "").rstrip("/")
    return {
        **raw,
        "public_url": public_url or None,
        "github_webhook_url": f"{public_url}/webhooks/github" if public_url else None,
        "webhook_secret_configured": bool(settings.github_webhook_secret),
        "accounts": settings.accounts_status(),
        "auto_sync_webhooks": settings.github_auto_sync_webhooks,
    }


@app.get("/", response_class=HTMLResponse)
async def event_dashboard() -> HTMLResponse:
    return HTMLResponse(DASHBOARD_HTML)


@app.get("/events")
async def events(limit: int = 50) -> list[dict[str, object]]:
    limit = max(1, min(limit, 250))
    return await storage.list_events(limit=limit)


@app.get("/events/{event_id}")
async def event_detail(event_id: int) -> dict[str, object]:
    event = await storage.get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return event


@app.get("/watched-prs")
async def watched_prs() -> list[dict[str, object]]:
    return await storage.list_watched_prs()


@app.get("/watched-repos")
async def watched_repos() -> list[dict[str, object]]:
    watches = await storage.list_watched_repos()
    return [enrich_watched_repo(watch) for watch in watches]


@app.post("/watched-repos")
async def add_watched_repo(
    payload: dict[str, str], background_tasks: BackgroundTasks
) -> dict[str, object]:
    url = payload.get("url", "").strip()
    parsed = _parse_github_repo_url(url)
    watch = await storage.add_watched_repo(
        url=url,
        owner=parsed["owner"],
        repo=parsed["repo"],
    )
    event_id = await storage.record_event(
        source="dashboard",
        event_type="repo_watch_added",
        delivery_id=None,
        status="queued",
        summary=f"Added repo watch and queued webhook setup for {watch['repo_full_name']}",
        details={"watched_repo": watch},
    )
    background_tasks.add_task(
        service.setup_webhook_for_repo_watch,
        watch_id=int(watch["id"]),
        event_id=event_id,
    )
    return enrich_watched_repo(watch)


@app.patch("/watched-repos/{watch_id}")
async def update_watched_repo(
    watch_id: int,
    payload: dict[str, Any],
    background_tasks: BackgroundTasks,
) -> dict[str, object]:
    if "enabled" not in payload:
        raise HTTPException(status_code=400, detail="enabled field is required")
    watch = await storage.get_watched_repo(watch_id)
    if not watch:
        raise HTTPException(status_code=404, detail="Watched repo not found")

    enabled = bool(payload["enabled"])
    await storage.set_watched_repo_enabled(watch_id, enabled=enabled)
    updated = await storage.get_watched_repo(watch_id)
    if updated is None:
        raise HTTPException(status_code=404, detail="Watched repo not found")

    if enabled:
        event_id = await storage.record_event(
            source="dashboard",
            event_type="repo_watch_enabled",
            delivery_id=None,
            status="queued",
            summary=f"Enabled watch for {updated['repo_full_name']} and queued webhook setup",
            details={"watched_repo": updated},
        )
        background_tasks.add_task(
            service.setup_webhook_for_repo_watch,
            watch_id=watch_id,
            event_id=event_id,
        )
    else:
        await storage.record_event(
            source="dashboard",
            event_type="repo_watch_disabled",
            delivery_id=None,
            status="disabled",
            summary=f"Disabled watch for {updated['repo_full_name']}",
            details={"watched_repo": updated},
        )
    return enrich_watched_repo(updated)


@app.delete("/watched-repos/{watch_id}")
async def delete_watched_repo(watch_id: int) -> dict[str, str]:
    deleted = await storage.delete_watched_repo(watch_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Watched repo not found")
    await storage.record_event(
        source="dashboard",
        event_type="repo_watch_removed",
        delivery_id=None,
        status="removed",
        summary=f"Removed watched repo {watch_id}",
        details={"watch_id": watch_id},
    )
    return {"status": "removed"}


@app.post("/watched-repos/{watch_id}/webhook")
async def setup_watched_repo_webhook(
    watch_id: int, background_tasks: BackgroundTasks
) -> dict[str, str]:
    watch = await storage.get_watched_repo(watch_id)
    if not watch:
        raise HTTPException(status_code=404, detail="Watched repo not found")
    event_id = await storage.record_event(
        source="dashboard",
        event_type="repo_webhook_setup",
        delivery_id=None,
        status="queued",
        summary=f"Queued webhook setup for {watch['repo_full_name']}",
        details={"watched_repo": watch},
    )
    background_tasks.add_task(
        service.setup_webhook_for_repo_watch, watch_id=watch_id, event_id=event_id
    )
    return {"status": "queued", "event_id": str(event_id)}


@app.post("/watched-prs")
async def add_watched_pr(
    payload: dict[str, str], background_tasks: BackgroundTasks
) -> dict[str, object]:
    url = payload.get("url", "").strip()
    parsed = _parse_github_pr_url(url)
    watch = await storage.add_watched_pr(
        url=url,
        owner=parsed["owner"],
        repo=parsed["repo"],
        pr_number=int(parsed["pr_number"]),
    )
    event_id = await storage.record_event(
        source="dashboard",
        event_type="watch_added",
        delivery_id=None,
        status="legacy",
        summary=(
            f"Added legacy PR watch without webhook setup for "
            f"{watch['repo_full_name']} PR #{watch['pr_number']}"
        ),
        details={"watched_pr": watch},
    )
    _ = event_id
    return watch


@app.delete("/watched-prs/{watch_id}")
async def delete_watched_pr(watch_id: int) -> dict[str, str]:
    deleted = await storage.delete_watched_pr(watch_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Watched PR not found")
    await storage.record_event(
        source="dashboard",
        event_type="watch_removed",
        delivery_id=None,
        status="removed",
        summary=f"Removed watched PR {watch_id}",
        details={"watch_id": watch_id},
    )
    return {"status": "removed"}


@app.post("/watched-prs/{watch_id}/poll")
async def poll_watched_pr(
    watch_id: int, background_tasks: BackgroundTasks
) -> dict[str, str]:
    watch = await storage.get_watched_pr(watch_id)
    if not watch:
        raise HTTPException(status_code=404, detail="Watched PR not found")
    event_id = await storage.record_event(
        source="poller",
        event_type="poll_now",
        delivery_id=None,
        status="queued",
        summary=f"Queued poll for {watch['repo_full_name']} PR #{watch['pr_number']}",
        details={"watched_pr": watch},
    )
    background_tasks.add_task(service.poll_watched_pr, watch_id=watch_id, event_id=event_id)
    return {"status": "queued", "event_id": str(event_id)}


@app.post("/watched-prs/{watch_id}/webhook")
async def setup_watched_pr_webhook(
    watch_id: int, background_tasks: BackgroundTasks
) -> dict[str, str]:
    _ = (watch_id, background_tasks)
    raise HTTPException(
        status_code=410,
        detail="PR-level webhooks were removed. Add the repository under Watched Repos.",
    )


@app.post("/webhooks/github")
async def github_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_github_event: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
) -> dict[str, str]:
    body = await request.body()
    delivery_id = request.headers.get("x-github-delivery")
    secret = (
        settings.github_webhook_secret.get_secret_value()
        if settings.github_webhook_secret
        else None
    )
    if not verify_github_signature(
        body=body, signature_header=x_hub_signature_256, webhook_secret=secret
    ):
        await storage.record_event(
            source="github",
            event_type=x_github_event or "unknown",
            delivery_id=delivery_id,
            status="rejected",
            summary="Rejected GitHub webhook because signature verification failed",
            details={"headers": _safe_headers(request.headers), "body": body.decode()},
        )
        raise HTTPException(status_code=401, detail="Invalid GitHub signature")
    payload: dict[str, Any] = await request.json()
    event_id = await storage.record_event(
        source="github",
        event_type=x_github_event or "unknown",
        delivery_id=delivery_id,
        status="received",
        summary=_github_summary(payload),
        details={"headers": _safe_headers(request.headers), "payload": payload},
    )
    if x_github_event == "issue_comment":
        background_tasks.add_task(service.handle_issue_comment, payload, event_id)
        return {"status": "queued", "event_id": str(event_id)}
    if x_github_event == "pull_request":
        background_tasks.add_task(service.handle_pull_request_event, payload, event_id)
        return {"status": "queued", "event_id": str(event_id)}
    if x_github_event not in {"issue_comment", "pull_request"}:
        await storage.update_event(
            event_id,
            status="ignored",
            summary=f"Ignored unsupported GitHub event {x_github_event}",
        )
        return {"status": "ignored", "event_id": str(event_id)}
    return {"status": "ignored", "event_id": str(event_id)}


@app.get("/debug/sessions")
async def debug_sessions() -> list[dict[str, object]]:
    return await storage.debug_dump()


def _safe_headers(headers: Any) -> dict[str, str]:
    redacted = {"authorization", "x-hub-signature-256"}
    return {
        key: ("<redacted>" if key.lower() in redacted else value)
        for key, value in dict(headers).items()
    }


def _github_summary(payload: dict[str, Any]) -> str:
    action = payload.get("action", "unknown")
    repo = (payload.get("repository") or {}).get("full_name", "unknown repo")
    pr = payload.get("pull_request") or {}
    if pr:
        return f"GitHub {action} pull_request on {repo} PR #{pr.get('number', '?')}"
    issue = payload.get("issue") or {}
    comment = payload.get("comment") or {}
    user = (comment.get("user") or {}).get("login", "unknown user")
    issue_number = issue.get("number", "?")
    return f"GitHub {action} comment from {user} on {repo} PR #{issue_number}"


def _parse_github_pr_url(url: str) -> dict[str, object]:
    match = re.match(
        r"^https://github\.com/([^/\s]+)/([^/\s]+)/pull/([0-9]+)(?:[/?#].*)?$",
        url,
    )
    if not match:
        raise HTTPException(
            status_code=400,
            detail="Expected a GitHub PR URL like https://github.com/owner/repo/pull/123",
        )
    owner, repo, pr_number = match.groups()
    return {"owner": owner, "repo": repo, "pr_number": int(pr_number)}


def _parse_github_repo_url(url: str) -> dict[str, str]:
    match = re.match(
        r"^https://github\.com/([^/\s]+)/([^/\s]+?)(?:\.git)?/?(?:[?#].*)?$",
        url,
    )
    if not match:
        raise HTTPException(
            status_code=400,
            detail="Expected a GitHub repo URL like https://github.com/owner/repo",
        )
    owner, repo = match.groups()
    return {"owner": owner, "repo": repo}


async def _tunnel_webhook_sync_loop() -> None:
    global _last_tunnel_webhook_url
    tick = 0
    while True:
        try:
            if settings.github_auto_sync_webhooks:
                webhook_url = service._current_github_webhook_url()
                if webhook_url:
                    url_changed = webhook_url != _last_tunnel_webhook_url
                    if url_changed:
                        _last_tunnel_webhook_url = webhook_url
                        await service.sync_enabled_repo_webhooks(
                            reason="tunnel_url_changed"
                        )
                    elif tick % 6 == 0:
                        await service.sync_enabled_repo_webhooks(reason="retry")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            await storage.record_event(
                source="sync",
                event_type="repo_webhook_auto_sync",
                delivery_id=None,
                status="failed",
                summary=f"Automatic webhook sync failed: {type(exc).__name__}",
                details={"error": str(exc)},
            )
        tick += 1
        await asyncio.sleep(5)


async def _poll_watched_prs_loop() -> None:
    while True:
        await asyncio.sleep(settings.github_poll_interval_seconds)
        for watch in await storage.list_watched_prs():
            if not watch.get("enabled"):
                continue
            event_id = await storage.record_event(
                source="poller",
                event_type="scheduled_poll",
                delivery_id=None,
                status="queued",
                summary=(
                    f"Scheduled poll for {watch['repo_full_name']} "
                    f"PR #{watch['pr_number']}"
                ),
                details={"watched_pr": watch},
            )
            try:
                await service.poll_watched_pr(watch_id=int(watch["id"]), event_id=event_id)
            except Exception as exc:
                await storage.update_event(
                    event_id,
                    status="failed",
                    summary=f"Scheduled poll failed: {type(exc).__name__}",
                    details_patch={"error": str(exc)},
                )
