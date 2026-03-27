$ErrorActionPreference = "Stop"

if (-not $env:BASE_URL) {
  $env:BASE_URL = "http://canvas.io.vn"
}

if (-not $env:COURSE_COUNT) {
  Write-Host "Choose a seed dataset size:"
  Write-Host "1) Small"
  Write-Host "2) Medium"
  Write-Host "3) Large"
  $profileChoice = Read-Host "Enter choice [1-3]"

  switch ($profileChoice) {
    "1" {
      $env:COURSE_COUNT = "2"
      $env:TEACHER_POOL_SIZE = "2"
      $env:STUDENT_POOL_SIZE = "10"
      $env:TEACHERS_PER_COURSE = "1"
      $env:STUDENTS_PER_COURSE = "5"
      $env:ASSIGNMENTS_PER_COURSE = "2"
      $env:PAGES_PER_COURSE = "1"
      $env:DISCUSSIONS_PER_COURSE = "1"
    }
    "2" {
      $env:COURSE_COUNT = "12"
      $env:TEACHER_POOL_SIZE = "8"
      $env:STUDENT_POOL_SIZE = "250"
      $env:TEACHERS_PER_COURSE = "2"
      $env:STUDENTS_PER_COURSE = "40"
      $env:ASSIGNMENTS_PER_COURSE = "8"
      $env:PAGES_PER_COURSE = "4"
      $env:DISCUSSIONS_PER_COURSE = "3"
    }
    "3" {
      $env:COURSE_COUNT = "20"
      $env:TEACHER_POOL_SIZE = "15"
      $env:STUDENT_POOL_SIZE = "600"
      $env:TEACHERS_PER_COURSE = "3"
      $env:STUDENTS_PER_COURSE = "80"
      $env:ASSIGNMENTS_PER_COURSE = "10"
      $env:PAGES_PER_COURSE = "5"
      $env:DISCUSSIONS_PER_COURSE = "4"
    }
    default {
      throw "Invalid choice: $profileChoice"
    }
  }
}

if (-not $env:API_TOKEN) {
  $secureToken = Read-Host "Enter Canvas API token" -AsSecureString
  $bstr = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureToken)
  try {
    $env:API_TOKEN = [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($bstr)
  } finally {
    [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($bstr)
  }
}

if (-not $env:SEED_PREFIX) {
  $defaultPrefix = "lt-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
  $enteredPrefix = Read-Host "Enter seed prefix [$defaultPrefix]"
  if ([string]::IsNullOrWhiteSpace($enteredPrefix)) {
    $env:SEED_PREFIX = $defaultPrefix
  } else {
    $env:SEED_PREFIX = $enteredPrefix
  }
}

Write-Host "Seeding Canvas data with prefix: $($env:SEED_PREFIX)"
Write-Host "Base URL: $($env:BASE_URL)"
Write-Host "Courses: $($env:COURSE_COUNT), Teachers: $($env:TEACHER_POOL_SIZE), Students: $($env:STUDENT_POOL_SIZE)"

if (Get-Command python3 -ErrorAction SilentlyContinue) {
  python3 "$PSScriptRoot\seed_canvas_data.py"
} elseif (Get-Command python -ErrorAction SilentlyContinue) {
  python "$PSScriptRoot\seed_canvas_data.py"
} elseif (Get-Command py -ErrorAction SilentlyContinue) {
  py -3 "$PSScriptRoot\seed_canvas_data.py"
} else {
  throw "Python is required. Install python3 or the Windows py launcher."
}
