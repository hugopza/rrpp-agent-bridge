$ErrorActionPreference = "Stop"

$root = Split-Path $PSScriptRoot -Parent
$python = Join-Path $root ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Local environment not found. Run: python -m venv .venv"
}

Push-Location $root
try {
    & $python -m rrpp_bridge migrate
    $worker = Start-Process -FilePath $python -ArgumentList @("-m", "rrpp_bridge", "worker") -WorkingDirectory $root -WindowStyle Hidden -PassThru
    $gmail = $null
    if (Test-Path -LiteralPath (Join-Path $root "secrets\gmail-token.json")) {
        $gmail = Start-Process -FilePath $python -ArgumentList @("-m", "rrpp_bridge", "gmail-poll") -WorkingDirectory $root -WindowStyle Hidden -PassThru
    }
    try {
        & $python -m rrpp_bridge web
    }
    finally {
        if ($gmail -and -not $gmail.HasExited) {
            Stop-Process -Id $gmail.Id
        }
        if ($worker -and -not $worker.HasExited) {
            Stop-Process -Id $worker.Id
        }
    }
}
finally {
    Pop-Location
}
