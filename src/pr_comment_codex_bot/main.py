from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

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


DASHBOARD_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Codex PR Debate Bot Dashboard</title>
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
      max-width: 1000px;
      display: flex;
      flex-direction: column;
      gap: 32px;
    }
    header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      padding: 0 10px;
    }
    header h1 {
      font-size: 24px;
      font-weight: 800;
      letter-spacing: -0.02em;
    }
    .dashboard-grid {
      display: grid;
      grid-template-columns: repeat(2, 1fr);
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
      transform: scale(1.01);
    }
    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
      width: 100%;
      margin-bottom: 16px;
    }
    .card-label {
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }
    
    /* Card Heights & Spans */
    .status-card, .tunnel-card {
      min-height: 180px;
      height: auto;
    }
    .repos-card, .config-card {
      min-height: 480px;
      height: auto;
    }
    .events-card {
      grid-column: 1 / -1;
      min-height: 560px;
      height: auto;
    }

    /* Common Form / Inputs inside cards */
    .card-select, .card-input {
      background: rgba(0, 0, 0, 0.25);
      border: 1px solid rgba(0, 0, 0, 0.15);
      border-radius: 999px;
      padding: 8px 16px;
      font-size: 12px;
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
      padding-right: 28px;
      background-image: url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' fill='none' stroke='%23ffffff' stroke-width='1.5'%3E%3Cpath d='M1 1l4 4 4-4'/%3E%3C/svg%3E");
      background-repeat: no-repeat;
      background-position: right 12px center;
    }

    /* Card 1: Status (Green) */
    .status-card {
      background: var(--accent-green);
      color: #000000;
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
      gap: 10px;
      overflow-y: auto;
      max-height: 280px;
      margin-top: 14px;
      padding-right: 4px;
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
      gap: 12px;
      margin-top: 14px;
      padding: 10px 14px;
      border-radius: 16px;
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
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--text-secondary);
    }
    .repos-toolbar-sub {
      font-size: 10px;
      font-weight: 600;
      color: var(--text-muted);
    }
    .repo-item {
      display: grid;
      grid-template-columns: minmax(0, 1fr) auto;
      gap: 12px;
      align-items: center;
      font-size: 12px;
      background: rgba(255, 255, 255, 0.03);
      padding: 10px 14px;
      border-radius: 16px;
      border: 1px solid rgba(255, 255, 255, 0.05);
    }
    .repo-actions {
      display: inline-flex;
      align-items: center;
      gap: 10px;
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
      font-size: 10px;
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
    .repo-sync {
      border: 1px solid rgba(255, 255, 255, 0.10);
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.06);
      color: var(--text-secondary);
      cursor: pointer;
      font-size: 10px;
      font-weight: 800;
      letter-spacing: 0.05em;
      padding: 5px 10px;
      text-transform: uppercase;
      transition: color 0.2s, border-color 0.2s, background 0.2s;
    }
    .repo-sync:hover {
      background: rgba(198, 255, 0, 0.12);
      border-color: rgba(198, 255, 0, 0.35);
      color: var(--accent-green);
    }
    .repo-add-form {
      display: flex;
      gap: 8px;
      margin-top: 14px;
    }
    .repo-add-input {
      flex: 1;
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 999px;
      padding: 10px 16px;
      font-size: 13px;
      color: var(--text-white);
      outline: none;
    }
    .repo-add-submit {
      background: var(--accent-green);
      color: #000000;
      border: none;
      border-radius: 999px;
      padding: 10px 20px;
      font-size: 13px;
      font-weight: 700;
      cursor: pointer;
      transition: opacity 0.2s;
    }
    .repo-add-submit:hover {
      opacity: 0.9;
    }

    /* Card 3: Webhook Tunnel (White) */
    .tunnel-card {
      background: #ffffff;
      color: #000000;
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
    .events-list {
      display: flex;
      flex-direction: column;
      gap: 10px;
      margin-top: 14px;
      overflow-y: auto;
      max-height: 460px;
      padding-right: 4px;
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
      padding: 12px 18px;
      border-radius: 18px;
      font-size: 12px;
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
      min-width: 0;
      flex: 1;
    }
    .event-id {
      font-family: monospace;
      font-weight: 700;
      color: var(--text-secondary);
    }
    .event-summary {
      color: var(--text-white);
      max-width: 100%;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .event-status {
      padding: 3px 8px;
      border-radius: 999px;
      font-size: 10px;
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

    /* Card 6: Bot Config / Settings (Orange, Tall) */
    .config-card {
      background: var(--accent-orange);
      color: #000000;
      border: none;
    }
    .config-card .card-select {
      background-image: url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' fill='none' stroke='%23000000' stroke-width='1.5'%3E%3Cpath d='M1 1l4 4 4-4'/%3E%3C/svg%3E");
    }
    .config-form {
      display: flex;
      flex-direction: column;
      gap: 16px;
      margin-top: 18px;
      flex: 1;
      justify-content: center;
    }
    .config-group {
      display: flex;
      flex-direction: column;
      gap: 6px;
    }
    .config-group label {
      font-size: 11px;
      font-weight: 800;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      opacity: 0.6;
    }
    .config-input-row {
      display: flex;
      gap: 8px;
    }
    .config-card .card-input, .config-card .card-select {
      flex: 1;
      background: rgba(0, 0, 0, 0.06);
      border: 1px solid rgba(0, 0, 0, 0.1);
      border-radius: 12px;
      padding: 10px 14px;
      font-size: 13px;
      font-weight: 600;
      color: #000000;
      outline: none;
    }
    .config-card .card-input::placeholder {
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
      padding: 8px 14px;
      font-size: 11px;
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
      font-size: 11px;
      font-weight: 700;
      opacity: 0.8;
      margin-top: 2px;
    }
    .config-submit-btn {
      background: #000000;
      color: var(--text-white);
      border: none;
      border-radius: 999px;
      padding: 12px 20px;
      font-size: 13px;
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
    .dashboard-footer a {
      color: var(--text-secondary);
      text-decoration: none;
      text-transform: uppercase;
    }
    .dashboard-footer a:hover {
      color: var(--accent-green);
    }

    /* Responsive scaling */
    @media (max-width: 900px) {
      .dashboard-grid {
        grid-template-columns: 1fr;
      }
      .events-card {
        grid-column: 1 / -1;
      }
    }
  </style>
</head>
<body>
  <div class="showcase-container">
    <header>
      <h1>PR Comment Codex Bot Dashboard</h1>
    </header>
    
    <div class="dashboard-grid">
      <!-- Card 1: System Status (Green) -->
      <div class="card status-card">
        <div class="card-header">
          <span class="card-label" style="color: #000000;">SYSTEM STATUS</span>
          <span class="status-pill" id="status-pill">OFFLINE</span>
        </div>
        <div class="status-value" id="status-val">↓ OFFLINE</div>
        <div class="status-sub" id="status-sub">No forwarding active</div>
      </div>

      <!-- Card 3: Webhook Tunnel (White) -->
      <div class="card tunnel-card">
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

      <!-- Card 2: Watched Repos (Dark) -->
      <div class="card repos-card">
        <div class="card-header">
          <span class="card-label" style="color: var(--text-secondary);">WATCHED REPOSITORIES</span>
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

      <!-- Card 6: Bot Config / Settings (Orange) -->
      <div class="card config-card">
        <div class="card-header">
          <span class="card-label" style="color: #000000;">BOT CONFIGURATION</span>
        </div>
        <form class="config-form" id="config-form">
          <div class="config-group">
            <label>Holder Account</label>
            <div class="config-input-row">
              <input class="card-input" type="text" id="holder-login" placeholder="holder_username" required>
            </div>
            <div class="config-pills" data-account="holder">
              <button type="button" class="config-pill active" data-auth="gh_cli">gh CLI</button>
              <button type="button" class="config-pill" data-auth="token">Token</button>
            </div>
            <div class="config-input-row" id="holder-token-row" style="display: none; margin-top: 6px;">
              <input class="card-input" type="password" id="holder-token" placeholder="GitHub Personal Access Token">
            </div>
            <div class="config-status-label" id="holder-status" style="color: rgba(0, 0, 0, 0.65);">Checking status...</div>
          </div>

          <div class="config-group">
            <label>Replier Account</label>
            <div class="config-input-row">
              <input class="card-input" type="text" id="replier-login" placeholder="replier_username" required>
            </div>
            <div class="config-pills" data-account="replier">
              <button type="button" class="config-pill active" data-auth="gh_cli">gh CLI</button>
              <button type="button" class="config-pill" data-auth="token">Token</button>
            </div>
            <div class="config-input-row" id="replier-token-row" style="display: none; margin-top: 6px;">
              <input class="card-input" type="password" id="replier-token" placeholder="GitHub Personal Access Token">
            </div>
            <div class="config-status-label" id="replier-status" style="color: rgba(0, 0, 0, 0.65);">Checking status...</div>
          </div>

          <div class="config-group">
            <label>Permissions Mode</label>
            <select class="card-select" id="holder-permission" style="width: 100%; font-weight: 700;">
              <option value="admin">Admin</option>
              <option value="write">Write</option>
              <option value="triage">Triage</option>
            </select>
          </div>

          <button class="config-submit-btn" type="submit">Save Settings</button>
        </form>
      </div>

      <!-- Card 4: Webhook Events Log (Dark, Full Width) -->
      <div class="card events-card">
        <div class="card-header">
          <span class="card-label" style="color: var(--text-secondary);">RECENT WEBHOOK EVENTS</span>
          <button type="button" class="card-select card-select-dark" id="refresh-btn" style="border: 1px solid rgba(255, 255, 255, 0.1); padding: 4px 12px; border-radius: 999px;">Refresh</button>
        </div>
        <div class="events-list" id="events-list">
          <div class="event-item" style="justify-content: center; color: var(--text-muted); cursor: default;">No events received</div>
        </div>
      </div>
    </div>

    <!-- Bottom showcase footer -->
    <div class="dashboard-footer">
      <span>@tou.visuals</span>
      <a href="/sessions">Current Sessions &gt;</a>
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
          return;
        }

        eventsList.innerHTML = allEvents.slice(0, 12).map(event => {
          const eventId = String(event.id ?? "");
          const isSuccess = ['received', 'polled', 'watched', 'created', 'updated', 'ready_to_implement', 'implementing', 'implemented'].includes(event.status);
          const statusClass = isSuccess ? 'status-success' : 'status-failed';
          return `
            <div class="event-item" data-id="${esc(eventId)}">
              <div class="event-left">
                <span class="event-id">${esc(eventId.slice(0, 8))}</span>
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
              <div class="repo-actions">
                <button class="repo-sync" data-sync="${esc(watch.id)}" title="Force webhook setup for this repo">Sync</button>
                <label class="repo-toggle" title="${enabled ? "Turn off this repo" : "Turn on this repo"}">
                  <input type="checkbox" data-toggle="${esc(watch.id)}" ${enabled ? "checked" : ""}>
                  <span class="repo-toggle-slider"></span>
                </label>
                <button class="repo-delete" data-delete="${esc(watch.id)}" title="Remove repo">×</button>
              </div>
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

        document.querySelectorAll("button[data-sync]").forEach(button => {
          button.addEventListener("click", async (e) => {
            e.stopPropagation();
            const watchId = button.getAttribute("data-sync");
            button.disabled = true;
            const originalText = button.textContent;
            button.textContent = "Syncing";
            try {
              const response = await fetch(`/watched-repos/${watchId}/webhook`, {
                method: "POST"
              });
              if (!response.ok) {
                const error = await response.json();
                throw new Error(error.detail || "Failed to sync webhook");
              }
              await refreshAll();
            } catch (err) {
              alert(err.message || "Failed to sync webhook");
            } finally {
              button.disabled = false;
              button.textContent = originalText;
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
</html>
"""
SESSIONS_HTML = """\n<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Current Sessions - Codex PR Debate Bot</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #080808;
      --panel: #141416;
      --panel-2: #1d1d20;
      --line: #2a2a2f;
      --text: #f8fafc;
      --muted: #9ca3af;
      --green: #c6ff00;
      --yellow: #ffe359;
      --blue: #85a9ff;
      --orange: #ff7c3b;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      background: var(--bg);
      color: var(--text);
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      padding: 32px 20px;
    }
    main {
      width: min(1120px, 100%);
      margin: 0 auto;
    }
    .topbar {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      margin-bottom: 28px;
    }
    h1 {
      margin: 0;
      font-size: clamp(30px, 5vw, 56px);
      letter-spacing: 0;
      line-height: 0.95;
    }
    .sub {
      color: var(--muted);
      margin-top: 10px;
      max-width: 660px;
      line-height: 1.5;
    }
    .nav {
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }
    .button,
    button {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 38px;
      padding: 0 14px;
      border-radius: 8px;
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--text);
      text-decoration: none;
      font-weight: 700;
      cursor: pointer;
    }
    .button.primary {
      background: var(--green);
      color: #050505;
      border-color: var(--green);
    }
    .sessions {
      display: grid;
      gap: 14px;
    }
    .session {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 10px;
      padding: 18px;
      display: grid;
      grid-template-columns: minmax(0, 1.2fr) minmax(260px, 0.8fr);
      gap: 18px;
    }
    .repo {
      font-size: 20px;
      font-weight: 800;
      margin-bottom: 8px;
      overflow-wrap: anywhere;
    }
    .meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      color: var(--muted);
      font-size: 13px;
    }
    .pill {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 4px 10px;
      background: #0e0e10;
    }
    .pill.status {
      color: #050505;
      background: var(--yellow);
      border-color: var(--yellow);
      font-weight: 800;
    }
    .threads {
      display: grid;
      gap: 10px;
    }
    .thread {
      background: var(--panel-2);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
      display: grid;
      gap: 10px;
    }
    .thread-head {
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
    }
    .thread-kind {
      font-weight: 800;
    }
    .thread-id {
      color: var(--muted);
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-size: 12px;
      overflow-wrap: anywhere;
    }
    .empty {
      border: 1px dashed var(--line);
      border-radius: 10px;
      color: var(--muted);
      padding: 32px;
      text-align: center;
    }
    .copy-note {
      color: var(--muted);
      font-size: 12px;
    }
    @media (max-width: 760px) {
      .topbar,
      .session {
        grid-template-columns: 1fr;
        display: grid;
      }
      .nav {
        width: 100%;
      }
      .button,
      button {
        flex: 1;
      }
    }
  </style>
</head>
<body>
  <main>
    <div class="topbar">
      <div>
        <h1>Current Sessions</h1>
        <p class="sub">Active PR conversations, their debate thread, and their implementation thread. Open a thread to inspect it in Codex.</p>
      </div>
      <nav class="nav">
        <a class="button" href="/">Dashboard</a>
        <button id="refresh">Refresh</button>
      </nav>
    </div>
    <div class="sessions" id="sessions">
      <div class="empty">Loading sessions...</div>
    </div>
  </main>

  <script>
    const sessionsEl = document.querySelector("#sessions");
    const refreshBtn = document.querySelector("#refresh");

    function esc(value) {
      return String(value ?? "").replace(/[&<>"']/g, ch => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[ch]));
    }

    function formatDate(value) {
      if (!value) return "unknown";
      const date = new Date(value);
      if (Number.isNaN(date.getTime())) return value;
      return date.toLocaleString();
    }

    function renderThread(thread) {
      if (!thread.thread_id) {
        return `
          <div class="thread">
            <div class="thread-head">
              <span class="thread-kind">${esc(thread.label)}</span>
              <span class="copy-note">not created yet</span>
            </div>
          </div>
        `;
      }
      return `
        <div class="thread">
          <div class="thread-head">
            <span class="thread-kind">${esc(thread.label)}</span>
            <a class="button primary" href="${esc(thread.open_url)}">Open in Codex</a>
          </div>
          <div class="thread-id">${esc(thread.thread_id)}</div>
        </div>
      `;
    }

    function renderSession(session) {
      return `
        <section class="session">
          <div>
            <div class="repo">${esc(session.repo_full_name)} #${esc(session.pr_number)}</div>
            <div class="meta">
              <span class="pill status">${esc(session.status)}</span>
              <span class="pill">Updated ${esc(formatDate(session.updated_at))}</span>
              ${session.branch ? `<span class="pill">Branch ${esc(session.branch)}</span>` : ""}
              ${session.commit_sha ? `<span class="pill">Commit ${esc(session.commit_sha.slice(0, 12))}</span>` : ""}
              <a class="pill button" href="${esc(session.github_pr_url)}" target="_blank" rel="noreferrer">GitHub PR</a>
            </div>
          </div>
          <div class="threads">
            ${session.threads.map(renderThread).join("")}
          </div>
        </section>
      `;
    }

    async function loadSessions() {
      const response = await fetch("/sessions/current", { cache: "no-store" });
      if (!response.ok) {
        sessionsEl.innerHTML = `<div class="empty">Could not load sessions.</div>`;
        return;
      }
      const sessions = await response.json();
      if (!sessions.length) {
        sessionsEl.innerHTML = `<div class="empty">No PR sessions yet. Trigger the bot from a watched PR comment first.</div>`;
        return;
      }
      sessionsEl.innerHTML = sessions.map(renderSession).join("");
    }

    refreshBtn.addEventListener("click", loadSessions);
    loadSessions();
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


@app.get("/sessions", response_class=HTMLResponse)
async def current_sessions_page() -> HTMLResponse:
    return HTMLResponse(SESSIONS_HTML)


@app.get("/sessions/current")
async def current_sessions() -> list[dict[str, object]]:
    sessions = await storage.list_sessions()
    return [_session_view(session) for session in sessions]


@app.get("/codex/threads/{thread_id}/open")
async def open_codex_thread(thread_id: str) -> RedirectResponse:
    if not await _is_known_thread_id(thread_id):
        raise HTTPException(status_code=404, detail="Codex thread is not tracked")
    return RedirectResponse(f"codex://threads/{quote(thread_id, safe='')}")


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


def _session_view(session: dict[str, object]) -> dict[str, object]:
    state = dict(session.get("state") or {})
    repo_full_name = str(session["repo_full_name"])
    pr_number = int(session["pr_number"])
    debate_thread_id = state.get("debate_thread_id")
    codex_thread_id = state.get("codex_thread_id")
    return {
        "repo_full_name": repo_full_name,
        "pr_number": pr_number,
        "github_pr_url": f"https://github.com/{repo_full_name}/pull/{pr_number}",
        "status": state.get("status") or "unknown",
        "updated_at": state.get("updated_at") or session.get("updated_at"),
        "branch": state.get("branch"),
        "commit_sha": state.get("commit_sha"),
        "threads": [
            _thread_view("debate", "Debate", debate_thread_id),
            _thread_view("implementation", "Implementation", codex_thread_id),
        ],
    }


def _thread_view(kind: str, label: str, thread_id: object) -> dict[str, object]:
    thread_id_str = str(thread_id) if thread_id else None
    return {
        "kind": kind,
        "label": label,
        "thread_id": thread_id_str,
        "open_url": (
            f"/codex/threads/{quote(thread_id_str, safe='')}/open"
            if thread_id_str
            else None
        ),
    }


async def _is_known_thread_id(thread_id: str) -> bool:
    sessions = await storage.list_sessions()
    for session in sessions:
        state = dict(session.get("state") or {})
        if thread_id in {
            state.get("debate_thread_id"),
            state.get("codex_thread_id"),
        }:
            return True
    return False


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
