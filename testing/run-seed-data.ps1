$ErrorActionPreference = "Stop"

if (-not $env:API_TOKEN) {
  throw "API_TOKEN is required"
}

if (-not $env:BASE_URL) {
  $env:BASE_URL = "http://canvas.io.vn:30080"
}

if (Get-Command python3 -ErrorAction SilentlyContinue) {
  python3 "$PSScriptRoot\seed_canvas_data.py"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
  python "$PSScriptRoot\seed_canvas_data.py"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
  py -3 "$PSScriptRoot\seed_canvas_data.py"
} else {
  throw "Python is required. Install python3 or the Windows py launcher."
}
