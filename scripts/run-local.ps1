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
    $maintenance = Start-Process -FilePath $python -ArgumentList @("-m", "rrpp_bridge", "maintenance") -WorkingDirectory $root -WindowStyle Hidden -PassThru
    try {
        & $python -m rrpp_bridge web
    }
    finally {
        if ($maintenance -and -not $maintenance.HasExited) {
            Stop-Process -Id $maintenance.Id
        }
        if ($worker -and -not $worker.HasExited) {
            Stop-Process -Id $worker.Id
        }
    }
}
finally {
    Pop-Location
}
