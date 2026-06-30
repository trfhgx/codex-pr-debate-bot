param(
    [switch]$SkipCloudflared
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
Set-Location $Root

function Section($Title) {
    Write-Host ""
    Write-Host "== $Title ==" -ForegroundColor Cyan
    Write-Host ""
}

function Has-Command($Name) {
    return $null -ne (Get-Command $Name -ErrorAction SilentlyContinue)
}

Section "Installing dependencies"

if (-not $SkipCloudflared) {
    if (Has-Command "cloudflared") {
        Write-Host "cloudflared already installed: $((Get-Command cloudflared).Source)"
    } elseif (Has-Command "winget") {
        Write-Host "Installing cloudflared with winget..."
        winget install --id Cloudflare.cloudflared --accept-package-agreements --accept-source-agreements
    } else {
        Write-Host "cloudflared is not installed and winget was not found."
        Write-Host "Install it manually or use another tunnel provider:"
        Write-Host "  https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
        Write-Host "  or set TUNNEL_PROVIDER=localtunnel/ngrok in .env"
    }
}

if (Has-Command "uv") {
    uv sync
} else {
    throw "uv not found. Install it from https://docs.astral.sh/uv/"
}

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example"
}

Section "Setup complete"
Write-Host "Next steps:"
Write-Host "  1. Configure holder/replier accounts in .env or the dashboard"
Write-Host "  2. Run: uv run python scripts/start_app.py"
Write-Host "  3. Open: http://127.0.0.1:8088/"
