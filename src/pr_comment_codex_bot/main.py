from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse

from .security import verify_github_signature
from .service import PRCommentService
from .settings import Settings
from .storage import Storage

settings = Settings()
storage = Storage(settings.database_path)
service = PRCommentService(settings=settings, storage=storage)
poll_task: asyncio.Task[None] | None = None

app = FastAPI(title=settings.app_name)


DASHBOARD_HTML = """
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>PR Comment Codex Bot Dashboard</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500&display=swap" rel="stylesheet">
  <style>
    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
    }
    :root {
      color-scheme: dark;
      --bg-color: #09090b;
      --card-bg: #18181b;
      --card-border: #27272a;
      --text-primary: #fafafa;
      --text-secondary: #a1a1aa;
      --accent-lime: #c5f935;
      --accent-orange: #ff7c3b;
      --accent-yellow: #ffe359;
      --accent-blue: #60a5fa;
    }
    body {
      background: var(--bg-color);
      color: var(--text-primary);
      font-family: 'Plus Jakarta Sans', ui-sans-serif, system-ui, -apple-system, sans-serif;
      padding: 40px 24px;
      min-height: 100vh;
      position: relative;
      overflow-x: hidden;
    }
    body::before {
      content: "";
      position: absolute;
      top: 0;
      left: 20%;
      width: 600px;
      height: 600px;
      background: radial-gradient(circle, rgba(197, 249, 53, 0.04) 0%, transparent 70%);
      z-index: -1;
      pointer-events: none;
    }
    body::after {
      content: "";
      position: absolute;
      bottom: 0;
      right: 10%;
      width: 500px;
      height: 500px;
      background: radial-gradient(circle, rgba(255, 124, 59, 0.03) 0%, transparent 70%);
      z-index: -1;
      pointer-events: none;
    }
    main {
      max-width: 1200px;
      margin: 0 auto;
      display: flex;
      flex-direction: column;
      gap: 32px;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
    }
    h1 {
      font-size: 28px;
      font-weight: 800;
      letter-spacing: -0.03em;
      margin-bottom: 6px;
      background: linear-gradient(to right, #fafafa, #a1a1aa);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }
    header p {
      color: var(--text-secondary);
      font-size: 14px;
      font-weight: 500;
    }
    .dashboard-grid {
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      grid-auto-rows: auto;
      gap: 24px;
    }
    .card {
      border-radius: 28px;
      padding: 24px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      position: relative;
      overflow: hidden;
      transition: transform 0.2s, box-shadow 0.2s;
    }
    .card:hover {
      transform: translateY(-2px);
    }
    .card-sm {
      height: 180px;
    }
    .card-md {
      height: 280px;
    }
    .card-lg {
      height: 584px;
      grid-row: span 2;
    }
    .card-header {
      display: flex;
      justify-content: space-between;
      align-items: center;
    }
    .card-label {
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.1em;
      text-transform: uppercase;
      opacity: 0.6;
    }
    .card-select {
      background: rgba(0, 0, 0, 0.05);
      border: 1px solid rgba(0, 0, 0, 0.1);
      border-radius: 999px;
      padding: 4px 12px;
      font-size: 11px;
      font-weight: 600;
      color: inherit;
      cursor: pointer;
      outline: none;
      appearance: none;
      -webkit-appearance: none;
      padding-right: 24px;
      background-image: url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' fill='none' stroke='%2309090b' stroke-width='1.5'%3E%3Cpath d='M1 1l4 4 4-4'/%3E%3C/svg%3E");
      background-repeat: no-repeat;
      background-position: right 10px center;
    }
    .card-select-dark {
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid rgba(255, 255, 255, 0.1);
      background-image: url("data:image/svg+xml;charset=utf-8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='10' height='6' fill='none' stroke='%23ffffff' stroke-width='1.5'%3E%3Cpath d='M1 1l4 4 4-4'/%3E%3C/svg%3E");
      color: var(--text-primary);
    }
    
    /* Card 1: Status Card */
    .status-card {
      background: var(--accent-lime);
      color: #09090b;
      grid-column: span 1;
      transition: background 0.5s ease;
    }
    .status-pill {
      background: rgba(9, 9, 11, 0.1);
      border: 1px solid rgba(9, 9, 11, 0.15);
      padding: 3px 10px;
      border-radius: 999px;
      font-size: 10px;
      font-weight: 700;
      letter-spacing: 0.05em;
    }
    .status-val {
      font-size: 38px;
      font-weight: 800;
      letter-spacing: -0.04em;
      line-height: 1.1;
      margin-top: 12px;
    }
    .status-sub {
      font-size: 12px;
      font-weight: 600;
      opacity: 0.8;
      margin-top: auto;
    }

    /* Card 2: Metrics Card */
    .metrics-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      grid-column: span 1;
    }
    .metrics-list {
      display: flex;
      flex-direction: column;
      gap: 10px;
      margin-top: 14px;
      flex: 1;
      justify-content: center;
    }
    .metric-item {
      display: flex;
      justify-content: space-between;
      font-size: 13px;
    }
    .metric-item span {
      opacity: 0.6;
    }
    .metric-item strong {
      font-weight: 700;
    }

    /* Card 3: Tunnel Card */
    .tunnel-card {
      background: #ffffff;
      color: #09090b;
      grid-column: span 2;
    }
    .tunnel-card-header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
    }
    .perf-bars {
      display: flex;
      align-items: flex-end;
      gap: 4px;
      height: 36px;
      margin-top: 10px;
      overflow: hidden;
      width: 160px;
    }
    .perf-bar {
      background: #60a5fa;
      border-radius: 999px;
      height: 100%;
      opacity: 0.85;
      flex-shrink: 0;
    }
    .tunnel-url-container {
      display: flex;
      align-items: center;
      background: rgba(9, 9, 11, 0.05);
      border: 1px solid rgba(9, 9, 11, 0.1);
      border-radius: 14px;
      padding: 10px 14px;
      margin-top: 14px;
      cursor: pointer;
      transition: all 0.2s;
    }
    .tunnel-url-container:hover {
      background: rgba(9, 9, 11, 0.09);
    }
    .tunnel-url {
      font-family: 'JetBrains Mono', monospace;
      font-size: 12px;
      font-weight: 500;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
      flex: 1;
    }
    .copy-icon {
      margin-left: 8px;
      opacity: 0.6;
      flex-shrink: 0;
    }

    /* Card 4: Chart Card */
    .chart-card {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      grid-column: span 2;
    }
    .chart-val-container {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      margin-top: 6px;
    }
    .chart-value {
      font-size: 32px;
      font-weight: 800;
      letter-spacing: -0.02em;
    }
    .chart-container {
      height: 120px;
      margin-top: 12px;
      position: relative;
    }
    .chart-labels {
      display: flex;
      justify-content: space-between;
      font-size: 11px;
      color: var(--text-secondary);
      margin-top: 6px;
      padding: 0 4px;
    }

    /* Card 5: Gauge Card */
    .gauge-card {
      background: var(--accent-yellow);
      color: #09090b;
      grid-column: span 2;
    }
    .gauge-container {
      position: relative;
      width: 160px;
      height: 80px;
      margin: 12px auto 0;
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
      background: rgba(9, 9, 11, 0.12);
      transform-origin: 50% 100%;
    }
    .tick.active {
      background: #09090b;
      width: 3px;
      height: 10px;
    }
    .gauge-value {
      position: absolute;
      bottom: 0;
      left: 0;
      width: 100%;
      text-align: center;
      font-size: 30px;
      font-weight: 800;
      color: #09090b;
      line-height: 1;
    }
    .gauge-sub-info {
      text-align: center;
      font-size: 12px;
      font-weight: 600;
      opacity: 0.8;
      margin-top: 10px;
    }

    /* Card 6: Daily Card (Orange) */
    .daily-card {
      background: var(--accent-orange);
      color: #09090b;
      grid-column: span 2;
    }
    .daily-columns {
      display: flex;
      gap: 14px;
      align-items: flex-end;
      height: 380px;
      margin-top: 24px;
    }
    .column-wrapper {
      display: flex;
      flex-direction: column;
      align-items: center;
      gap: 10px;
      flex: 1;
      height: 100%;
      justify-content: flex-end;
    }
    .column-track {
      width: 20px;
      height: 100%;
      max-height: 320px;
      background: rgba(9, 9, 11, 0.12);
      border-radius: 999px;
      position: relative;
      overflow: hidden;
      display: flex;
      align-items: flex-end;
    }
    .column-fill {
      width: 100%;
      background: #09090b;
      border-radius: 999px;
      transition: height 0.6s cubic-bezier(0.16, 1, 0.3, 1);
    }
    .column-label {
      font-size: 11px;
      font-weight: 700;
      opacity: 0.8;
      text-transform: uppercase;
    }

    /* Form & Input Elements */
    form {
      display: flex;
      gap: 12px;
      margin-top: 12px;
      flex-wrap: wrap;
    }
    input[type="url"] {
      flex: 1;
      min-width: 280px;
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 14px;
      padding: 12px 18px;
      color: var(--text-primary);
      font-family: inherit;
      font-size: 14px;
      transition: border-color 0.2s, box-shadow 0.2s;
    }
    input[type="url"]:focus {
      outline: none;
      border-color: var(--accent-lime);
      box-shadow: 0 0 0 3px rgba(197, 249, 53, 0.15);
    }
    .btn {
      border: none;
      border-radius: 14px;
      padding: 12px 24px;
      font-size: 14px;
      font-weight: 700;
      cursor: pointer;
      transition: all 0.2s;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      font-family: inherit;
    }
    .btn-sm {
      padding: 8px 16px;
      font-size: 12px;
      border-radius: 10px;
    }
    .btn-primary {
      background: var(--accent-lime);
      color: #09090b;
    }
    .btn-primary:hover {
      background: #d4ff3b;
      transform: translateY(-1px);
      box-shadow: 0 4px 12px rgba(197, 249, 53, 0.2);
    }
    .btn-secondary {
      background: rgba(255, 255, 255, 0.08);
      color: var(--text-primary);
      border: 1px solid rgba(255, 255, 255, 0.1);
    }
    .btn-secondary:hover {
      background: rgba(255, 255, 255, 0.12);
    }
    .btn-danger {
      background: rgba(239, 68, 68, 0.1);
      color: #f87171;
      border: 1px solid rgba(239, 68, 68, 0.2);
    }
    .btn-danger:hover {
      background: rgba(239, 68, 68, 0.2);
      transform: translateY(-1px);
    }

    /* Section & Table Styling */
    section {
      background: var(--card-bg);
      border: 1px solid var(--card-border);
      border-radius: 28px;
      padding: 32px;
    }
    .section-header {
      margin-bottom: 20px;
    }
    .section-header h2 {
      font-size: 20px;
      font-weight: 800;
      letter-spacing: -0.02em;
    }
    .section-header p {
      color: var(--text-secondary);
      font-size: 13px;
      margin-top: 4px;
    }
    .table-container {
      border: 1px solid var(--card-border);
      border-radius: 18px;
      overflow: hidden;
      background: rgba(9, 9, 11, 0.4);
      margin-top: 18px;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      text-align: left;
    }
    th {
      background: rgba(255, 255, 255, 0.02);
      color: var(--text-secondary);
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      padding: 14px 20px;
      border-bottom: 1px solid var(--card-border);
    }
    td {
      padding: 16px 20px;
      border-bottom: 1px solid var(--card-border);
      color: var(--text-primary);
      font-size: 14px;
      vertical-align: middle;
    }
    tr:last-child td {
      border-bottom: none;
    }
    tr[data-id] {
      cursor: pointer;
    }
    tr[data-id]:hover td {
      background: rgba(255, 255, 255, 0.02);
    }
    
    /* Status Badge Colors */
    .status {
      display: inline-flex;
      align-items: center;
      font-size: 10px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      padding: 4px 10px;
      border-radius: 999px;
      white-space: nowrap;
    }
    .status.failed, .status.blocked, .status.rejected {
      background: rgba(239, 68, 68, 0.1);
      color: #f87171;
      border: 1px solid rgba(239, 68, 68, 0.2);
    }
    .status.ignored, .status.removed {
      background: rgba(161, 161, 170, 0.1);
      color: #a1a1aa;
      border: 1px solid rgba(161, 161, 170, 0.2);
    }
    .status.implementing, .status.ready_to_implement {
      background: rgba(59, 130, 246, 0.1);
      color: #60a5fa;
      border: 1px solid rgba(59, 130, 246, 0.2);
    }
    .status.polled, .status.watched, .status.initialized, .status.created, .status.updated, .status.received {
      background: rgba(34, 197, 94, 0.1);
      color: #4ade80;
      border: 1px solid rgba(34, 197, 94, 0.2);
    }
    .status.missing_tunnel, .status.queued, .status.pending {
      background: rgba(234, 179, 8, 0.1);
      color: #facc15;
      border: 1px solid rgba(234, 179, 8, 0.2);
    }

    .empty {
      border: 2px dashed var(--card-border);
      border-radius: 18px;
      padding: 40px;
      color: var(--text-secondary);
      text-align: center;
      font-size: 14px;
      font-weight: 500;
      background: rgba(9, 9, 11, 0.2);
    }
    .mono {
      font-family: 'JetBrains Mono', monospace;
      font-size: 13px;
    }
    .font-semibold {
      font-weight: 600;
    }
    .opacity-80 {
      opacity: 0.8;
    }
    .opacity-60 {
      opacity: 0.6;
    }

    /* Dialog Modal Styling */
    dialog {
      background: rgba(24, 24, 27, 0.95);
      border: 1px solid var(--card-border);
      border-radius: 24px;
      padding: 32px;
      color: var(--text-primary);
      max-width: 800px;
      width: 90%;
      box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.6);
      backdrop-filter: blur(16px);
      margin: auto;
    }
    dialog::backdrop {
      background: rgba(9, 9, 11, 0.8);
      backdrop-filter: blur(8px);
    }
    .detail-head h3 {
      font-size: 20px;
      font-weight: 800;
      letter-spacing: -0.02em;
    }
    pre {
      background: #09090b;
      border: 1px solid var(--card-border);
      border-radius: 16px;
      padding: 20px;
      overflow-x: auto;
      font-family: 'JetBrains Mono', monospace;
      font-size: 13px;
      color: var(--text-secondary);
      max-height: 480px;
      margin-top: 18px;
    }

    /* Pulsing Active Dot */
    .active-dot-pulsing {
      width: 8px;
      height: 8px;
      background-color: #10b981;
      border-radius: 50%;
      box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7);
      animation: pulse 1.6s infinite;
    }
    @keyframes pulse {
      0% {
        transform: scale(0.95);
        box-shadow: 0 0 0 0 rgba(16, 185, 129, 0.7);
      }
      70% {
        transform: scale(1);
        box-shadow: 0 0 0 6px rgba(16, 185, 129, 0);
      }
      100% {
        transform: scale(0.95);
        box-shadow: 0 0 0 0 rgba(16, 185, 129, 0);
      }
    }

    /* Custom scrollbars */
    ::-webkit-scrollbar {
      width: 8px;
      height: 8px;
    }
    ::-webkit-scrollbar-track {
      background: var(--bg-color);
    }
    ::-webkit-scrollbar-thumb {
      background: var(--card-border);
      border-radius: 999px;
    }
    ::-webkit-scrollbar-thumb:hover {
      background: #3f3f46;
    }

    /* Responsiveness overrides */
    @media (max-width: 1024px) {
      .dashboard-grid {
        grid-template-columns: repeat(2, 1fr);
      }
      .card-lg {
        grid-row: span 1;
        height: 380px;
      }
      .daily-columns {
        height: 180px;
      }
    }
    @media (max-width: 640px) {
      body {
        padding: 24px 16px;
      }
      .dashboard-grid {
        grid-template-columns: 1fr;
        gap: 16px;
      }
      .card {
        grid-column: span 1 !important;
        height: auto !important;
        min-height: 180px;
      }
      .card-lg {
        min-height: 300px;
      }
      .daily-columns {
        height: 140px;
      }
      header {
        flex-direction: column;
        align-items: flex-start;
        gap: 12px;
      }
      header button {
        align-self: stretch;
      }
    }
  </style>
</head>
<body>
  <main>
    <header>
      <div>
        <h1>PR Comment Codex Bot</h1>
        <p id="meta">Listening for GitHub PR events.</p>
      </div>
      <button type="button" id="refresh" class="btn btn-secondary">Refresh</button>
    </header>

    <div class="dashboard-grid">
      <!-- Card 1: System Status (Green) -->
      <div class="card card-sm status-card">
        <div class="card-header">
          <span class="card-label" style="color: #09090b;">PROGRESS</span>
          <span class="status-pill">ONLINE</span>
        </div>
        <div class="status-val">↑ ACTIVE</div>
        <div class="status-sub">Tunnel active</div>
      </div>

      <!-- Card 2: Metrics List (Dark) -->
      <div class="card card-sm metrics-card">
        <div class="card-header">
          <span class="card-label">SYSTEM METRICS</span>
          <svg width="16" height="4" viewBox="0 0 16 4" fill="none" stroke="currentColor" stroke-width="2" style="opacity: 0.6;"><circle cx="2" cy="2" r="1"></circle><circle cx="8" cy="2" r="1"></circle><circle cx="14" cy="2" r="1"></circle></svg>
        </div>
        <div class="metrics-list">
          <div class="metric-item"><span>Watched Repos</span><strong>0</strong></div>
          <div class="metric-item"><span>Active PRs</span><strong>0</strong></div>
          <div class="metric-item"><span>Processed</span><strong>0</strong></div>
        </div>
      </div>

      <!-- Card 3: Performance (White) -->
      <div class="card card-sm tunnel-card" id="tunnel">
        <div class="tunnel-card-header">
          <div>
            <span class="card-label" style="color: #09090b;">WEBHOOK TUNNEL</span>
          </div>
          <div class="perf-bars">
            <div class="perf-bar" style="width: 8px; height: 40%;"></div>
            <div class="perf-bar" style="width: 12px; height: 60%;"></div>
            <div class="perf-bar" style="width: 6px; height: 35%;"></div>
            <div class="perf-bar" style="width: 14px; height: 75%;"></div>
            <div class="perf-bar" style="width: 10px; height: 50%;"></div>
            <div class="perf-bar" style="width: 18px; height: 90%;"></div>
            <div class="perf-bar" style="width: 12px; height: 70%;"></div>
            <div class="perf-bar" style="width: 8px; height: 45%;"></div>
          </div>
        </div>
        <div class="tunnel-url-container">
          <span class="tunnel-url">Checking tunnel...</span>
        </div>
      </div>

      <!-- Card 4: Analytics Line Chart (Dark) -->
      <div class="card card-md chart-card">
        <div class="card-header">
          <span class="card-label">ANALYTICS</span>
          <select class="card-select card-select-dark">
            <option>This Month</option>
          </select>
        </div>
        <div class="chart-val-container">
          <div class="chart-value">0+</div>
        </div>
        <div class="chart-container">
          <svg id="activity-svg" width="100%" height="100%" viewBox="0 0 360 100" preserveAspectRatio="none"></svg>
        </div>
        <div class="chart-labels">
          <span>Jan-Mar</span>
          <span>Apr-Jun</span>
          <span>Jul-Sep</span>
          <span>Oct-Dec</span>
        </div>
      </div>

      <!-- Card 6: Daily Columns (Orange, Tall) -->
      <div class="card card-lg daily-card">
        <div class="card-header">
          <span class="card-label" style="color: #09090b;">DAILY DISTRIBUTION</span>
          <select class="card-select">
            <option>This Week</option>
          </select>
        </div>
        <div style="font-size: 36px; font-weight: 800; letter-spacing: -0.02em; margin-top: 10px;">5K+</div>
        <div class="daily-columns">
          <!-- Populated by JS -->
        </div>
      </div>

      <!-- Card 5: Success Gauge (Yellow) -->
      <div class="card card-md gauge-card">
        <div class="card-header">
          <span class="card-label" style="color: #09090b;">DELIVERY RATE</span>
          <select class="card-select">
            <option>This Month</option>
          </select>
        </div>
        <div class="gauge-container">
          <div class="gauge-ticks">
            <!-- Ticks generated in JS -->
          </div>
          <div class="gauge-value">100%</div>
        </div>
        <div class="gauge-sub-info">Success - 100% &nbsp;•&nbsp; Failed - 0%</div>
      </div>
    </div>

    <!-- Watched Repos Section -->
    <section>
      <div class="section-header">
        <h2>Watched Repositories</h2>
        <p>Paste repository URL below. The bot automatically manages the webhook triggers.</p>
      </div>
      <form id="watch-form">
        <input id="watch-url" type="url" placeholder="https://github.com/owner/repo" required>
        <button type="submit" class="btn btn-primary">Add Repository</button>
      </form>
      <div id="watches"></div>
    </section>

    <!-- Events Log Section -->
    <section>
      <div class="section-header">
        <h2>System Webhook Events</h2>
        <p>Real-time log of events, comments, and deployment states.</p>
      </div>
      <div id="events"></div>
    </section>
  </main>

  <dialog id="detail" closedby="any" aria-labelledby="detail-title">
    <div class="detail-head" style="display: flex; justify-content: space-between; align-items: center; border-bottom: 1px solid var(--card-border); padding-bottom: 14px;">
      <h3 id="detail-title">Event Detail</h3>
      <button type="button" id="close" class="btn btn-secondary btn-sm">Close</button>
    </div>
    <pre id="detail-json"></pre>
  </dialog>

  <script>
    const eventsEl = document.querySelector("#events");
    const watchesEl = document.querySelector("#watches");
    const metaEl = document.querySelector("#meta");
    const detail = document.querySelector("#detail");
    const detailJson = document.querySelector("#detail-json");
    const detailTitle = document.querySelector("#detail-title");
    const tunnelEl = document.querySelector("#tunnel");

    let allEvents = [];
    let allWatches = [];
    let allPrs = [];

    function esc(value) {
      return String(value ?? "").replace(/[&<>"']/g, ch => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
      }[ch]));
    }

    async function copyToClipboard(text, el) {
      try {
        await navigator.clipboard.writeText(text);
        const originalContent = el.innerHTML;
        el.innerHTML = '<span class="tunnel-url" style="color: #059669; font-weight: 700;">Copied webhook URL!</span>';
        setTimeout(() => {
          el.innerHTML = originalContent;
        }, 2000);
      } catch (err) {
        console.error('Failed to copy: ', err);
      }
    }

    async function refreshAll() {
      await Promise.all([
        loadEvents(),
        loadWatches(),
        loadTunnelInfo()
      ]);
    }

    async function loadEvents() {
      try {
        const response = await fetch("/events?limit=100", { cache: "no-store" });
        allEvents = await response.json();
        metaEl.textContent = `Listening. ${allEvents.length} event${allEvents.length === 1 ? "" : "s"} recorded.`;
        
        if (!allEvents.length) {
          eventsEl.innerHTML = '<div class="empty">No events received yet.</div>';
          updateStats();
          return;
        }

        eventsEl.innerHTML = `
          <div class="table-container">
            <table>
              <thead>
                <tr>
                  <th>ID</th>
                  <th>Received</th>
                  <th>Source</th>
                  <th>Type</th>
                  <th>Status</th>
                  <th>Summary</th>
                </tr>
              </thead>
              <tbody>
                ${allEvents.map(event => `
                  <tr data-id="${esc(event.id)}">
                    <td class="mono font-semibold">${esc(event.id)}</td>
                    <td class="opacity-80">${esc(event.received_at ? event.received_at.replace('T', ' ').slice(0, 19) : '')}</td>
                    <td class="mono opacity-80">${esc(event.source)}</td>
                    <td class="mono opacity-80">${esc(event.event_type)}</td>
                    <td><span class="status ${esc(event.status)}">${esc(event.status)}</span></td>
                    <td>${esc(event.summary)}</td>
                  </tr>
                `).join("")}
              </tbody>
            </table>
          </div>
        `;
        
        document.querySelectorAll("tr[data-id]").forEach(row => {
          row.addEventListener("click", async () => {
            const id = row.getAttribute("data-id");
            const detailResponse = await fetch(`/events/${id}`, { cache: "no-store" });
            const event = await detailResponse.json();
            detailTitle.textContent = `Event ${id}`;
            detailJson.textContent = JSON.stringify(event, null, 2);
            detail.showModal();
          });
        });

        updateStats();
      } catch (err) {
        console.error("Error loading events: ", err);
      }
    }

    async function loadWatches() {
      try {
        const [watchesRes, prsRes] = await Promise.all([
          fetch("/watched-repos", { cache: "no-store" }),
          fetch("/watched-prs", { cache: "no-store" })
        ]);
        allWatches = await watchesRes.json();
        allPrs = await prsRes.json();

        if (!allWatches.length) {
          watchesEl.innerHTML = '<div class="empty">No watched repos yet.</div>';
          updateStats();
          return;
        }

        watchesEl.innerHTML = `
          <div class="table-container">
            <table>
              <thead>
                <tr>
                  <th>Repository</th>
                  <th>Webhook Status</th>
                  <th>Summary</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                ${allWatches.map(watch => `
                  <tr>
                    <td>
                      <a href="${esc(watch.url)}" target="_blank" rel="noreferrer" class="repo-link">
                        ${esc(watch.repo_full_name)}
                      </a>
                    </td>
                    <td>
                      <span class="status ${esc(watch.last_webhook_status || "pending")}">${esc(watch.last_webhook_status || "pending")}</span>
                      ${watch.webhook_url ? `<br><small class="mono opacity-60">${esc(watch.webhook_url)}</small>` : ""}
                    </td>
                    <td class="opacity-80">${esc(watch.last_webhook_summary || "")}</td>
                    <td>
                      <div style="display: flex; gap: 8px;">
                        <button type="button" class="btn btn-secondary btn-sm" data-webhook="${esc(watch.id)}">Setup webhook</button>
                        <button type="button" class="btn btn-danger btn-sm" data-delete="${esc(watch.id)}">Remove</button>
                      </div>
                    </td>
                  </tr>
                `).join("")}
              </tbody>
            </table>
          </div>
        `;

        document.querySelectorAll("button[data-webhook]").forEach(button => {
          button.addEventListener("click", async (e) => {
            e.stopPropagation();
            await fetch(`/watched-repos/${button.getAttribute("data-webhook")}/webhook`, { method: "POST" });
            await refreshAll();
          });
        });

        document.querySelectorAll("button[data-delete]").forEach(button => {
          button.addEventListener("click", async (e) => {
            e.stopPropagation();
            if (confirm("Are you sure you want to remove this repo?")) {
              await fetch(`/watched-repos/${button.getAttribute("data-delete")}`, { method: "DELETE" });
              await refreshAll();
            }
          });
        });

        updateStats();
      } catch (err) {
        console.error("Error loading watches: ", err);
      }
    }

    async function loadTunnelInfo() {
      try {
        const response = await fetch("/tunnel-info", { cache: "no-store" });
        const info = await response.json();
        
        // Update Card 1 (Status)
        const statusCard = document.querySelector('.status-card');
        if (statusCard) {
          if (info.public_url) {
            statusCard.style.background = 'var(--accent-lime)';
            statusCard.querySelector('.status-val').textContent = '↑ ACTIVE';
            statusCard.querySelector('.status-sub').textContent = 'Tunnel connection healthy';
            statusCard.querySelector('.status-pill').textContent = 'ONLINE';
          } else {
            statusCard.style.background = '#fca5a5';
            statusCard.querySelector('.status-val').textContent = '↓ OFFLINE';
            statusCard.querySelector('.status-sub').textContent = 'No active tunnel detected';
            statusCard.querySelector('.status-pill').textContent = 'OFFLINE';
          }
        }

        if (info.public_url) {
          const secretText = info.webhook_secret_configured
            ? "Webhook secret configured"
            : "Webhook secret missing";
          tunnelEl.innerHTML = `
            <div class="tunnel-card-header">
              <div>
                <span class="card-label" style="color: #09090b; display: flex; align-items: center; gap: 6px;">
                  WEBHOOK TUNNEL <span class="active-dot-pulsing"></span>
                </span>
                <p style="font-size: 11px; opacity: 0.7; margin-top: 4px; font-weight: 600;">${esc(info.provider || "tunnel")} active • ${esc(secretText)}</p>
              </div>
              <div class="perf-bars">
                <div class="perf-bar" style="width: 8px; height: 60%;"></div>
                <div class="perf-bar" style="width: 12px; height: 40%;"></div>
                <div class="perf-bar" style="width: 6px; height: 75%;"></div>
                <div class="perf-bar" style="width: 14px; height: 95%;"></div>
                <div class="perf-bar" style="width: 10px; height: 50%;"></div>
                <div class="perf-bar" style="width: 18px; height: 80%;"></div>
              </div>
            </div>
            <div class="tunnel-url-container" title="Click to copy Webhook URL" onclick="copyToClipboard('${esc(info.github_webhook_url)}', this)">
              <span class="tunnel-url">${esc(info.github_webhook_url)}</span>
              <svg class="copy-icon" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M8 7v12a2 2 0 002 2h8a2 2 0 002-2V7a2 2 0 00-2-2h-8a2 2 0 00-2 2zM8 7H6a2 2 0 00-2 2v10a2 2 0 002 2h2M16 5V3a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2h2" stroke-linecap="round" stroke-linejoin="round"></svg>
            </div>
          `;
        } else {
          tunnelEl.innerHTML = `
            <div class="tunnel-card-header">
              <div>
                <span class="card-label" style="color: #09090b;">WEBHOOK TUNNEL</span>
                <p style="font-size: 11px; opacity: 0.7; margin-top: 4px; font-weight: 600;">No forwarding active</p>
              </div>
              <div class="perf-bars">
                <div class="perf-bar" style="width: 8px; height: 20%; background: #ef4444; opacity: 0.5;"></div>
                <div class="perf-bar" style="width: 12px; height: 15%; background: #ef4444; opacity: 0.5;"></div>
                <div class="perf-bar" style="width: 6px; height: 25%; background: #ef4444; opacity: 0.5;"></div>
              </div>
            </div>
            <div class="tunnel-url-container" title="Click to copy local webhook URL" onclick="copyToClipboard('http://127.0.0.1:8088/webhooks/github', this)">
              <span class="tunnel-url">http://127.0.0.1:8088/webhooks/github</span>
              <svg class="copy-icon" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24"><path d="M8 7v12a2 2 0 002 2h8a2 2 0 002-2V7a2 2 0 00-2-2h-8a2 2 0 00-2 2zM8 7H6a2 2 0 00-2 2v10a2 2 0 002 2h2M16 5V3a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2h2" stroke-linecap="round" stroke-linejoin="round"></svg>
            </div>
          `;
        }
      } catch (err) {
        console.error("Error loading tunnel: ", err);
      }
    }

    function updateStats() {
      loadStats(allEvents, allWatches, allPrs);
    }

    function loadStats(events, watches, prs) {
      const total = events.length;
      const successes = events.filter(e => ['received', 'polled', 'watched', 'created', 'updated', 'ready_to_implement', 'implementing', 'implemented'].includes(e.status)).length;
      const successRate = total > 0 ? Math.round((successes / total) * 100) : 100;

      // Update Card 2 (Metrics)
      const metricsList = document.querySelector('.metrics-list');
      if (metricsList) {
        metricsList.innerHTML = `
          <div class="metric-item"><span>Watched Repos</span><strong>${watches.length}</strong></div>
          <div class="metric-item"><span>Active PRs</span><strong>${prs.length}</strong></div>
          <div class="metric-item"><span>Total Events</span><strong>${total}</strong></div>
          <div class="metric-item"><span>Success Rate</span><strong>${successRate}%</strong></div>
        `;
      }

      // Update Card 4 (Line Chart label)
      const chartValue = document.querySelector('.chart-value');
      if (chartValue) {
        chartValue.textContent = `${total}+`;
      }

      // Update Card 5 (Gauge)
      const gaugeVal = document.querySelector('.gauge-value');
      if (gaugeVal) {
        gaugeVal.textContent = `${successRate}%`;
      }
      const gaugeSub = document.querySelector('.gauge-sub-info');
      if (gaugeSub) {
        gaugeSub.innerHTML = `Success - ${successRate}% &nbsp;•&nbsp; Failed - ${100 - successRate}%`;
      }
      
      // Re-draw gauge ticks
      const gaugeTicks = document.querySelector('.gauge-ticks');
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
          tick.style.transform = `rotate(${angle}deg) translateY(-60px)`;
          const percentThreshold = (i / (totalTicks - 1)) * 100;
          if (percentThreshold <= successRate) {
            tick.classList.add('active');
          }
          gaugeTicks.appendChild(tick);
        }
      }

      // Update Card 6 (Daily Events)
      const dailyColumns = document.querySelector('.daily-columns');
      if (dailyColumns) {
        const days = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
        const today = new Date();
        const last7Days = [];
        for (let i = 6; i >= 0; i--) {
          const d = new Date();
          d.setDate(today.getDate() - i);
          last7Days.push({
            name: days[d.getDay()],
            dateStr: d.toISOString().split('T')[0],
            count: 0
          });
        }
        
        events.forEach(e => {
          if (e.received_at) {
            const dateStr = e.received_at.split('T')[0];
            const found = last7Days.find(d => d.dateStr === dateStr);
            if (found) found.count++;
          }
        });
        
        const maxCount = Math.max(...last7Days.map(d => d.count), 1);
        dailyColumns.innerHTML = last7Days.map(d => {
          const fillPercent = Math.max(8, Math.min(100, (d.count / maxCount) * 100));
          return `
            <div class="column-wrapper">
              <div class="column-track">
                <div class="column-fill" style="height: ${fillPercent}%"></div>
              </div>
              <div class="column-label">${d.name}</div>
            </div>
          `;
        }).join('');
      }

      // Redraw activity chart
      drawActivityChart(events);
    }

    function drawActivityChart(events) {
      const svg = document.querySelector('#activity-svg');
      if (!svg) return;
      
      let points = [30, 45, 25, 60, 40, 75]; // Beautiful template wave if database is empty
      
      if (events.length > 0) {
        const chunkCount = 6;
        const chunkSize = Math.max(1, Math.ceil(events.length / chunkCount));
        points = [];
        for (let i = chunkCount - 1; i >= 0; i--) {
          const slice = events.slice(i * chunkSize, (i + 1) * chunkSize);
          points.push(slice.length * 15 + 15);
        }
      }
      
      const width = 360;
      const height = 100;
      const xStep = width / (points.length - 1);
      
      const coords = points.map((p, index) => {
        const x = index * xStep;
        const y = height - Math.max(10, Math.min(90, p));
        return { x, y };
      });
      
      let d = `M ${coords[0].x} ${coords[0].y}`;
      for (let i = 0; i < coords.length - 1; i++) {
        const curr = coords[i];
        const next = coords[i+1];
        const cpX1 = curr.x + xStep / 2;
        const cpY1 = curr.y;
        const cpX2 = next.x - xStep / 2;
        const cpY2 = next.y;
        d += ` C ${cpX1} ${cpY1}, ${cpX2} ${cpY2}, ${next.x} ${next.y}`;
      }
      
      let dArea = d + ` L ${coords[coords.length - 1].x} ${height} L ${coords[0].x} ${height} Z`;
      
      svg.innerHTML = `
        <defs>
          <linearGradient id="chart-glow" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stop-color="var(--accent-lime)" stop-opacity="0.35"></stop>
            <stop offset="100%" stop-color="var(--accent-lime)" stop-opacity="0"></stop>
          </linearGradient>
          <filter id="shadow" x="-10%" y="-10%" width="120%" height="120%">
            <feDropShadow dx="0" dy="4" stdDeviation="4" flood-color="var(--accent-lime)" flood-opacity="0.45"></feDropShadow>
          </filter>
        </defs>
        <path d="${dArea}" fill="url(#chart-glow)"></path>
        <path d="${d}" fill="none" stroke="var(--accent-lime)" stroke-width="4" stroke-linecap="round" filter="url(#shadow)"></path>
        ${coords.map(c => `
          <circle cx="${c.x}" cy="${c.y}" r="5" fill="var(--accent-lime)" stroke="#18181b" stroke-width="2.5"></circle>
        `).join('')}
      `;
    }

    document.querySelector("#watch-form").addEventListener("submit", async event => {
      event.preventDefault();
      const input = document.querySelector("#watch-url");
      const response = await fetch("/watched-repos", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: input.value })
      });
      if (!response.ok) {
        alert(await response.text());
        return;
      }
      input.value = "";
      await refreshAll();
    });

    document.querySelector("#refresh").addEventListener("click", refreshAll);
    document.querySelector("#close").addEventListener("click", () => detail.close());
    
    // Light-dismiss fallback for Safari
    if (!('closedBy' in HTMLDialogElement.prototype)) {
      detail.addEventListener('click', (event) => {
        if (event.target !== detail) return;
        const rect = detail.getBoundingClientRect();
        const isDialogContent = (
          rect.top <= event.clientY &&
          event.clientY <= rect.top + rect.height &&
          rect.left <= event.clientX &&
          event.clientX <= rect.left + rect.width
        );
        if (isDialogContent) return;
        detail.close();
      });
    }

    refreshAll();
    setInterval(loadEvents, 3000);
    setInterval(loadWatches, 5000);
    setInterval(loadTunnelInfo, 5000);
  </script>
</body>
</html>
"""


@app.on_event("startup")
async def startup() -> None:
    await storage.init()
    global poll_task
    if settings.github_poll_interval_seconds > 0:
        poll_task = asyncio.create_task(_poll_watched_prs_loop())


@app.on_event("shutdown")
async def shutdown() -> None:
    if poll_task:
        poll_task.cancel()


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/tunnel-info")
async def tunnel_info() -> dict[str, object]:
    if not settings.tunnel_info_path.exists():
        return {
            "public_url": None,
            "github_webhook_url": None,
            "provider": None,
            "status": "not_started",
            "webhook_secret_configured": bool(settings.github_webhook_secret),
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
        }
    public_url = str(raw.get("public_url") or "").rstrip("/")
    return {
        **raw,
        "public_url": public_url or None,
        "github_webhook_url": f"{public_url}/webhooks/github" if public_url else None,
        "webhook_secret_configured": bool(settings.github_webhook_secret),
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
    return await storage.list_watched_repos()


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
    return watch


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
