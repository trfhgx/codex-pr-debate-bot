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

function Confirm-YesNo($Prompt) {
    $answer = Read-Host "$Prompt [y/N]"
    return $answer -match '^(y|yes)$'
}

function Get-EnvValue($Key) {
    if (-not (Test-Path ".env")) {
        return ""
    }
    $line = Get-Content ".env" | Where-Object { $_ -like "$Key=*" } | Select-Object -Last 1
    if (-not $line) {
        return ""
    }
    return $line.Substring($Key.Length + 1)
}

function Set-EnvValues($Updates) {
    $json = $Updates | ConvertTo-Json -Compress
    $env:JSON_PAYLOAD = $json
    uv run python -c "import json, os; from pathlib import Path; from pr_comment_codex_bot.env_file import update_env_values; update_env_values(Path('.env'), json.loads(os.environ['JSON_PAYLOAD']))"
    Remove-Item Env:\JSON_PAYLOAD -ErrorAction SilentlyContinue
}

function Configure-ActivationPhrase {
    $current = Get-EnvValue "GITHUB_TRIGGER_PHRASE"
    if ([string]::IsNullOrEmpty($current)) {
        $current = "codex"
    }

    Section "Activation phrase"
    Write-Host "The activation phrase decides which PR comments the bot responds to."
    Write-Host ""
    Write-Host "Press Enter to keep the default: codex"
    Write-Host "Type your own phrase to change it, for example: @codex-bot"
    Write-Host "Type NONE to trigger on every PR comment on watched repos."
    Write-Host ""

    $phrase = Read-Host "Activation phrase [$current]"
    if ([string]::IsNullOrEmpty($phrase)) {
        $phrase = $current
    }
    if ($phrase -ceq "NONE" -or $phrase -ceq "none") {
        $phrase = ""
    }

    Set-EnvValues @{ GITHUB_TRIGGER_PHRASE = $phrase }
    if ([string]::IsNullOrEmpty($phrase)) {
        Write-Host "Saved activation phrase: every PR comment on watched repos" -ForegroundColor Green
    } else {
        Write-Host "Saved activation phrase: $phrase" -ForegroundColor Green
    }
}

function Show-WingetInstallHelp {
    Write-Host "Install winget by installing Microsoft App Installer:"
    Write-Host "  https://aka.ms/getwinget"
    Write-Host "Then reopen PowerShell and rerun:"
    Write-Host "  .\scripts\setup.ps1"
}

function Install-Winget {
    Write-Host "winget was not found. It is distributed by Microsoft as App Installer."
    if (-not (Confirm-YesNo "Download and install winget automatically now?")) {
        Show-WingetInstallHelp
        return $false
    }

    $installer = Join-Path $env:TEMP "Microsoft.DesktopAppInstaller.msixbundle"
    try {
        Write-Host "Downloading winget installer..."
        Invoke-WebRequest -Uri "https://aka.ms/getwinget" -OutFile $installer
        Write-Host "Installing winget..."
        Add-AppxPackage -Path $installer
    } catch {
        Write-Host "Automatic winget install failed: $($_.Exception.Message)" -ForegroundColor Yellow
        Show-WingetInstallHelp
        return $false
    }

    if (Has-Command "winget") {
        Write-Host "winget installed: $((Get-Command winget).Source)"
        return $true
    }

    Write-Host "winget installation finished, but winget is not visible in this shell yet." -ForegroundColor Yellow
    Write-Host "Reopen PowerShell and rerun setup, or install cloudflared manually."
    return $false
}

Section "Installing dependencies"

if (-not $SkipCloudflared) {
    if (Has-Command "cloudflared") {
        Write-Host "cloudflared already installed: $((Get-Command cloudflared).Source)"
    } elseif ((Has-Command "winget") -or (Install-Winget)) {
        Write-Host "Installing cloudflared with winget..."
        winget install --id Cloudflare.cloudflared --accept-package-agreements --accept-source-agreements
    } else {
        Write-Host "cloudflared is not installed."
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

Configure-ActivationPhrase

Section "Setup complete"
Write-Host "Next steps:"
Write-Host "  1. Configure holder/replier accounts in .env or the dashboard"
Write-Host "  2. Run: uv run python scripts/start_app.py"
Write-Host "  3. Open: http://127.0.0.1:8088/"
